from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
pytest.importorskip("telegram")
from telegram.error import BadRequest

from genti.connectors.telegram import TelegramDashboardConnector
from genti.exceptions import FatalPipelineError
from genti.models import DashboardUpdate, LiveFeedState


class DummyUser:
    def __init__(self, user_id: int):
        self.id = user_id


class DummyMessage:
    def __init__(self, message_id: int, *, from_user: DummyUser | None = None):
        self.message_id = message_id
        self.from_user = from_user


class DummyChat:
    def __init__(self, chat_id: int | str, *, pinned_message: DummyMessage | None = None):
        self.id = chat_id
        self.pinned_message = pinned_message


class DummyBot:
    def __init__(
        self,
        *,
        send_exception: Exception | None = None,
        edit_exception: Exception | None = None,
        get_chat_exception: Exception | None = None,
        chat_id: int | str = 123,
        bot_id: int = 999,
        pinned_message: DummyMessage | None = None,
        delete_exception: Exception | None = None,
    ):
        self._send_exception = send_exception
        self._edit_exception = edit_exception
        self._get_chat_exception = get_chat_exception
        self._delete_exception = delete_exception
        self.sent_messages = []
        self.edited_messages = []
        self.pinned = []
        self.deleted_messages = []
        self.get_chat_called = 0
        self._chat_id = chat_id
        self._pinned_message = pinned_message
        self.id = bot_id
        self._next_message_id = 1

    async def get_chat(self, **kwargs):
        self.get_chat_called += 1
        if self._get_chat_exception is not None:
            raise self._get_chat_exception
        return DummyChat(self._chat_id, pinned_message=self._pinned_message)

    async def send_message(self, **kwargs):
        if self._send_exception is not None:
            raise self._send_exception
        self.sent_messages.append(kwargs)
        message = DummyMessage(message_id=self._next_message_id)
        self._next_message_id += 1
        return message

    async def edit_message_text(self, **kwargs):
        if self._edit_exception is not None:
            raise self._edit_exception
        self.edited_messages.append(kwargs)
        return DummyMessage(message_id=kwargs["message_id"])

    async def pin_chat_message(self, **kwargs):
        self.pinned.append(kwargs)

    async def delete_message(self, **kwargs):
        if self._delete_exception is not None:
            raise self._delete_exception
        self.deleted_messages.append(kwargs)


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

    with pytest.raises(FatalPipelineError, match="Telegram channel is unavailable"):
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

    with pytest.raises(FatalPipelineError, match="Telegram channel is unavailable"):
        await connector._send_message("payload")


@pytest.mark.asyncio
async def test_reuses_existing_pinned_dashboard():
    pinned = DummyMessage(message_id=42, from_user=DummyUser(user_id=1))
    bot = DummyBot(pinned_message=pinned, bot_id=1)
    application = SimpleNamespace(bot=bot)
    connector = TelegramDashboardConnector(application=application, channel_id="123")

    update = DashboardUpdate(
        dashboard_text="new text",
        new_live_messages=[],
        state=LiveFeedState(live=[], upcoming=[]),
        generated_at=datetime.now(timezone.utc),
    )

    await connector.push(update)

    assert not bot.sent_messages
    assert bot.edited_messages
    assert bot.edited_messages[0]["message_id"] == 42


@pytest.mark.asyncio
async def test_pin_dashboard_deletes_pin_notification():
    bot = DummyBot()
    application = SimpleNamespace(bot=bot)
    connector = TelegramDashboardConnector(application=application, channel_id="123")

    update = DashboardUpdate(
        dashboard_text="new text",
        new_live_messages=[],
        state=LiveFeedState(live=[], upcoming=[]),
        generated_at=datetime.now(timezone.utc),
    )

    await connector.push(update)

    assert bot.pinned
    assert bot.deleted_messages
    assert bot.deleted_messages[0]["message_id"] == 2
