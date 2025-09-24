"""Telegram connector that updates the dashboard channel."""
from __future__ import annotations

import logging
from typing import Optional

from telegram import Message, constants
from telegram.error import TelegramError
from telegram.ext import Application

from genti.models import DashboardUpdate


class TelegramDashboardConnector:
    """Publishes dashboard updates and notifications to Telegram."""

    def __init__(
        self,
        application: Application,
        channel_id: str,
        *,
        parse_mode: constants.ParseMode = constants.ParseMode.MARKDOWN_V2,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._application = application
        self._channel_id = channel_id
        self._parse_mode = parse_mode
        self._logger = logger or logging.getLogger(__name__)
        self._dashboard_message_id: Optional[int] = None

    async def push(self, update: DashboardUpdate) -> None:
        message = await self._ensure_dashboard()
        await self._edit_dashboard(message, update.dashboard_text)
        for text in update.new_live_messages:
            await self._send_message(text)

    async def _ensure_dashboard(self) -> Message:
        if self._dashboard_message_id is not None:
            try:
                message = await self._application.bot.edit_message_text(
                    chat_id=self._channel_id,
                    message_id=self._dashboard_message_id,
                    text="инициализация…",
                    parse_mode=self._parse_mode,
                    disable_web_page_preview=True,
                )
                return message
            except TelegramError:
                self._logger.warning(
                    "Failed to reuse dashboard message %s, creating a new one",
                    self._dashboard_message_id,
                    exc_info=True,
                )
                self._dashboard_message_id = None

        message = await self._application.bot.send_message(
            chat_id=self._channel_id,
            text="инициализация…",
            parse_mode=self._parse_mode,
            disable_web_page_preview=True,
        )
        self._dashboard_message_id = message.message_id
        await self._pin_dashboard()
        return message

    async def _pin_dashboard(self) -> None:
        try:
            await self._application.bot.pin_chat_message(
                chat_id=self._channel_id,
                message_id=self._dashboard_message_id,
                disable_notification=True,
            )
        except TelegramError:
            self._logger.warning("Unable to pin dashboard message", exc_info=True)

    async def _edit_dashboard(self, message: Message, text: str) -> None:
        await self._application.bot.edit_message_text(
            chat_id=self._channel_id,
            message_id=message.message_id,
            text=text,
            parse_mode=self._parse_mode,
            disable_web_page_preview=True,
        )

    async def _send_message(self, text: str) -> None:
        await self._application.bot.send_message(
            chat_id=self._channel_id,
            text=text,
            parse_mode=self._parse_mode,
            disable_web_page_preview=False,
        )
