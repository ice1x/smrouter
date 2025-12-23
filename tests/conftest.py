import sys
import types
from pathlib import Path
import asyncio
import inspect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_telegram_stubs() -> None:
    try:
        import telegram  # noqa: F401

        return
    except ImportError:
        pass

    telegram_module = types.ModuleType("telegram")

    class TelegramError(Exception):
        ...

    class BadRequest(TelegramError):
        def __init__(self, message: str):
            super().__init__(message)
            self.message = message

    error_module = types.ModuleType("telegram.error")
    error_module.BadRequest = BadRequest
    error_module.TelegramError = TelegramError

    constants_module = types.ModuleType("telegram.constants")

    class _ParseMode:
        MARKDOWN_V2 = "MarkdownV2"

    constants_module.ParseMode = _ParseMode

    helpers_module = types.ModuleType("telegram.helpers")

    def escape_markdown(text: str, *_, version: int | None = None, **__) -> str:
        if version == 2:
            escape_chars = r"_*[]()~`>#+-=|{}.!"
        else:
            escape_chars = "\\*_[]"
        escaped = "".join(f"\\{char}" if char in escape_chars else char for char in text)
        return escaped

    helpers_module.escape_markdown = escape_markdown

    class Update:
        def __init__(self, *, user=None, chat=None, message=None):
            self.effective_user = user
            self.effective_chat = chat
            self.message = message

    telegram_module.Update = Update
    telegram_module.constants = constants_module
    telegram_module.error = error_module

    class _ContextTypes:
        DEFAULT_TYPE = object

    class CommandHandler:
        def __init__(self, *_, **__):
            ...

    class _JobQueue:
        def run_repeating(self, *_args, **_kwargs):
            ...

    class _Updater:
        async def start_polling(self, *_, **__):
            ...

        async def stop(self, *_, **__):
            ...

    class Application:
        def __init__(self):
            self.bot = None
            self.job_queue = _JobQueue()
            self.updater = _Updater()

        def add_handler(self, *_args, **__kwargs):
            ...

        async def initialize(self):
            ...

        async def start(self):
            ...

        async def stop(self):
            ...

        async def shutdown(self):
            ...

    class ApplicationBuilder:
        def token(self, *_args, **__kwargs):
            return self

        def build(self) -> Application:
            return Application()

    class _ContextTypesContainer:
        DEFAULT_TYPE = object

    ext_module = types.ModuleType("telegram.ext")
    ext_module.Application = Application
    ext_module.ApplicationBuilder = ApplicationBuilder
    ext_module.CommandHandler = CommandHandler
    ext_module.ContextTypes = _ContextTypesContainer

    telegram_module.ext = ext_module

    class Chat:
        def __init__(self, chat_id=None, pinned_message=None):
            self.id = chat_id
            self.pinned_message = pinned_message

    class Message:
        def __init__(self, message_id=None, from_user=None):
            self.message_id = message_id
            self.from_user = from_user

    telegram_module.Chat = Chat
    telegram_module.Message = Message

    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.error"] = error_module
    sys.modules["telegram.constants"] = constants_module
    sys.modules["telegram.ext"] = ext_module
    sys.modules["telegram.helpers"] = helpers_module


_install_telegram_stubs()


def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        asyncio.run(pyfuncitem.obj(**pyfuncitem.funcargs))
        return True
    return None
