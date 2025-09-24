"""Telegram connector that updates the dashboard channel."""
from __future__ import annotations

import logging
from typing import Optional, Union

from telegram import Chat, Message, constants
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application

from genti.exceptions import FatalPipelineError
from genti.models import DashboardUpdate


ChatId = Union[int, str]


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
        self._resolved_chat_id: Optional[ChatId] = None
        self._resolved_chat: Optional[Chat] = None

    async def push(self, update: DashboardUpdate) -> None:
        message = await self._ensure_dashboard()
        await self._edit_dashboard(message, update.dashboard_text)
        for text in update.new_live_messages:
            await self._send_message(text)

    async def _target_chat_id(self) -> ChatId:
        await self._get_chat()
        if self._resolved_chat_id is None:
            raise RuntimeError("Chat identifier could not be resolved")
        return self._resolved_chat_id

    async def _get_chat(self) -> Chat:
        if self._resolved_chat is not None:
            return self._resolved_chat

        try:
            chat = await self._application.bot.get_chat(chat_id=self._channel_id)
        except BadRequest as exc:
            if exc.message and "chat not found" in exc.message.lower():
                raise FatalPipelineError(
                    "Телеграм-канал недоступен: проверьте идентификатор канала и права бота"
                ) from exc
            raise

        chat_id = getattr(chat, "id", None)
        self._resolved_chat_id = chat_id if chat_id is not None else self._channel_id
        self._resolved_chat = chat
        return chat

    async def _ensure_dashboard(self) -> Message:
        await self._ensure_dashboard_message_id()

        if self._dashboard_message_id is not None:
            try:
                message = await self._application.bot.edit_message_text(
                    chat_id=await self._target_chat_id(),
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

        try:
            message = await self._application.bot.send_message(
                chat_id=await self._target_chat_id(),
                text="инициализация…",
                parse_mode=self._parse_mode,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if exc.message and "chat not found" in exc.message.lower():
                raise FatalPipelineError(
                    "Телеграм-канал недоступен: проверьте идентификатор канала и права бота"
                ) from exc
            raise
        self._dashboard_message_id = message.message_id
        await self._pin_dashboard()
        return message

    async def _ensure_dashboard_message_id(self) -> None:
        if self._dashboard_message_id is not None:
            return

        chat = await self._get_chat()
        pinned_message = getattr(chat, "pinned_message", None)
        if not pinned_message:
            return

        message_id = getattr(pinned_message, "message_id", None)
        if message_id is None:
            return

        bot_id = getattr(self._application.bot, "id", None)
        from_user = getattr(pinned_message, "from_user", None)
        author_id = getattr(from_user, "id", None)
        if bot_id is not None and author_id is not None and bot_id != author_id:
            self._logger.debug("Pinned message does not belong to the bot; ignoring")
            return

        self._dashboard_message_id = message_id

    async def _pin_dashboard(self) -> None:
        try:
            await self._application.bot.pin_chat_message(
                chat_id=await self._target_chat_id(),
                message_id=self._dashboard_message_id,
                disable_notification=True,
            )
        except TelegramError:
            self._logger.warning("Unable to pin dashboard message", exc_info=True)

    async def _edit_dashboard(self, message: Message, text: str) -> None:
        try:
            await self._application.bot.edit_message_text(
                chat_id=await self._target_chat_id(),
                message_id=message.message_id,
                text=text,
                parse_mode=self._parse_mode,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if exc.message and "message is not modified" in exc.message.lower():
                self._logger.debug("Dashboard message unchanged; skipping edit")
                return
            raise

    async def _send_message(self, text: str) -> None:
        try:
            await self._application.bot.send_message(
                chat_id=await self._target_chat_id(),
                text=text,
                parse_mode=self._parse_mode,
                disable_web_page_preview=False,
            )
        except BadRequest as exc:
            if exc.message and "chat not found" in exc.message.lower():
                raise FatalPipelineError(
                    "Телеграм-канал недоступен: проверьте идентификатор канала и права бота"
                ) from exc
            raise
