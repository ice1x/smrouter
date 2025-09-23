import asyncio
import inspect
import logging
import sys
import types
from pathlib import Path

if "telegram" not in sys.modules:
    telegram_module = types.ModuleType("telegram")
    telegram_module.Message = type("Message", (), {})
    telegram_module.Chat = type("Chat", (), {})

    constants_module = types.ModuleType("telegram.constants")

    class ParseMode:
        MARKDOWN = "Markdown"

    constants_module.ParseMode = ParseMode
    telegram_module.constants = constants_module

    sys.modules["telegram"] = telegram_module
    sys.modules["telegram.constants"] = constants_module

    ext_module = types.ModuleType("telegram.ext")

    class Application:
        def __init__(self):
            self.post_init = []
            self.bot = types.SimpleNamespace()

    class ApplicationBuilder:
        def token(self, token):
            return self

        def build(self):
            return Application()

    class CommandHandler:
        def __init__(self, command, callback):
            self.command = command
            self.callback = callback

    ext_module.Application = Application
    ext_module.ApplicationBuilder = ApplicationBuilder
    ext_module.CommandHandler = CommandHandler

    sys.modules["telegram.ext"] = ext_module

if "aiohttp" not in sys.modules:
    aiohttp_module = types.ModuleType("aiohttp")

    class ClientSession:  # pragma: no cover - simple stub
        def __init__(self, *args, **kwargs):
            pass

    class ClientTimeout:  # pragma: no cover - simple stub
        def __init__(self, *args, **kwargs):
            pass

    aiohttp_module.ClientSession = ClientSession
    aiohttp_module.ClientTimeout = ClientTimeout
    sys.modules["aiohttp"] = aiohttp_module

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import attach_update_cycle


class DummyApp:
    def __init__(self, post_init=None):
        self.post_init = post_init


def test_attach_update_cycle_registers_post_init_and_schedules(caplog):
    app = DummyApp()
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)

    with caplog.at_level(logging.INFO):
        attach_update_cycle(app, create_task=fake_create_task)
        assert isinstance(app.post_init, list)
        assert len(app.post_init) == 1
        on_start = app.post_init[0]
        asyncio.run(on_start(app))

    assert scheduled, "update_cycle coroutine should be scheduled"
    coro = scheduled[0]
    assert inspect.iscoroutine(coro)
    assert coro.cr_code.co_name == "update_cycle"
    frame = coro.cr_frame
    assert frame is not None
    assert frame.f_locals.get("app") is app
    assert "Application post-init: запускаем цикл обновления" in caplog.text
    coro.close()


def test_attach_update_cycle_preserves_existing_callbacks():
    def existing_callback(app):
        raise RuntimeError("should not run in test")

    existing_list = [existing_callback]
    app = DummyApp(post_init=list(existing_list))

    attach_update_cycle(app)

    assert app.post_init[:1] == existing_list
    assert callable(app.post_init[1])
