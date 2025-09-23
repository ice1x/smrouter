import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "aiohttp" not in sys.modules:
    aiohttp_stub = types.ModuleType("aiohttp")

    class _ClientSession:  # pragma: no cover - тестовый стаб
        pass

    class _ClientTimeout:  # pragma: no cover - тестовый стаб
        def __init__(self, *args, **kwargs):
            pass

    aiohttp_stub.ClientSession = _ClientSession
    aiohttp_stub.ClientTimeout = _ClientTimeout
    sys.modules["aiohttp"] = aiohttp_stub

telegram_ext = pytest.importorskip("telegram.ext")
main_module = pytest.importorskip("main")
register_post_init_hook = main_module.register_post_init_hook
Application = telegram_ext.Application
ApplicationBuilder = telegram_ext.ApplicationBuilder


@pytest.mark.asyncio
async def test_register_post_init_restores_container_from_plain_list():
    app: Application = ApplicationBuilder().token("123:ABC").build()

    events: list[str] = []

    async def existing_callback(_: Application) -> None:
        events.append("existing")

    async def new_callback(_: Application) -> None:
        events.append("new")

    # Симулируем замену контейнера на обычный список.
    app._post_init = [existing_callback]  # type: ignore[attr-defined]

    register_post_init_hook(app, new_callback)

    assert not isinstance(app.post_init, list)

    for callback in list(app.post_init):
        await callback(app)

    assert events == ["existing", "new"]


@pytest.mark.asyncio
async def test_register_post_init_initializes_from_none():
    app: Application = ApplicationBuilder().token("123:ABC").build()

    events: list[str] = []

    async def new_callback(_: Application) -> None:
        events.append("new")

    app._post_init = None  # type: ignore[attr-defined]

    register_post_init_hook(app, new_callback)

    for callback in list(app.post_init):
        await callback(app)

    assert events == ["new"]
