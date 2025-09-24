from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
pytest.importorskip("telegram")
from telegram.error import BadRequest

from genti.connectors.telegram import TelegramDashboardConnector
from genti.exceptions import FatalPipelineError
from genti.models import DashboardUpdate, LiveFeedState


class DummyMessage:
    def __init__(self, message_id: int):
        self.message_id = message_id


class DummyChat:
    def __init__(self, chat_id: int | str):
        self.id = chat_id


class DummyBot:
    def __init__(
        self,
        *,
        send_exception: Exception | None = None,
        edit_exception: Exception | None = None,
        get_chat_exception: Exception | None = None,
        chat_id: int | str = 123,
    ):
        self._send_exception = send_exception
        self._edit_exception = edit_exception
        self._get_chat_exception = get_chat_exception
        self.sent_messages = []
        self.edited_messages = []
        self.pinned = []
        self.get_chat_called = 0
        self._chat_id = chat_id

    async def get_chat(self, **kwargs):
        self.get_chat_called += 1
        if self._get_chat_exception is not None:
            raise self._get_chat_exception
        return DummyChat(self._chat_id)

    async def send_message(self, **kwargs):
        if self._send_exception is not None:
            raise self._send_exception
        self.sent_messages.append(kwargs)
        return DummyMessage(message_id=1)

    async def edit_message_text(self, **kwargs):
        if self._edit_exception is not None:
            raise self._edit_exception
        self.edited_messages.append(kwargs)
        return DummyMessage(message_id=kwargs["message_id"])

    async def pin_chat_message(self, **kwargs):
        self.pinned.append(kwargs)


@pytest.mark.asyncio
async def test_dashboard_creation_raises_clear_error_on_missing_channel():
    bot = DummyBot(get_chat_exception=BadRequest("Chat not found"))
    application = SimpleNamespace(bot=bot)
    connector = TelegramDashboardConnector(application=application, channel_id="123")

    update = DashboardUpdate(
        dashboard_text="test",
        new_live_messages=[],
        state=LiveFeedState(live=[], upcoming=[]),
        generated_at=datetime.now(timezone.utc),
    )

    with pytest.raises(FatalPipelineError, match="Телеграм-канал недоступен"):
        await connector.push(update)


@pytest.mark.asyncio
async def test_edit_dashboard_ignores_message_not_modified(caplog):
    bot = DummyBot(edit_exception=BadRequest("Message is not modified"))
    application = SimpleNamespace(bot=bot)
    connector = TelegramDashboardConnector(application=application, channel_id="123")
    connector._dashboard_message_id = 1  # simulate existing dashboard

    message = DummyMessage(message_id=1)

    caplog.set_level("DEBUG")
    await connector._edit_dashboard(message, "same")

    assert any("unchanged" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_resolves_channel_id_only_once():
    bot = DummyBot(chat_id=-100)
    application = SimpleNamespace(bot=bot)
    connector = TelegramDashboardConnector(application=application, channel_id="@demo")
    connector._dashboard_message_id = 1

    message = DummyMessage(message_id=1)
    await connector._edit_dashboard(message, "text")
    await connector._send_message("payload")

    assert bot.get_chat_called == 1


@pytest.mark.asyncio
async def test_send_message_propagates_channel_error():
    bot = DummyBot(send_exception=BadRequest("Chat not found"))
    application = SimpleNamespace(bot=bot)
    connector = TelegramDashboardConnector(application=application, channel_id="123")

    with pytest.raises(FatalPipelineError, match="Телеграм-канал недоступен"):
        await connector._send_message("payload")
