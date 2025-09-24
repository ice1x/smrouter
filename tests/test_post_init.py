import asyncio
import inspect
from types import SimpleNamespace

import pytest

pytest.importorskip("aiohttp")

from main import register_post_init_hook


class FakeCallbackData:
    """Minimal stand-in for PTB's CallbackList container."""

    def __init__(self):
        self.callbacks = []

    def append(self, callback):
        self.callbacks.append(callback)

    async def __call__(self, application):
        for callback in self.callbacks:
            result = callback(application)
            if inspect.isawaitable(result):
                await result


def test_register_post_init_hook_handles_missing_container(monkeypatch):
    app = SimpleNamespace()
    container = FakeCallbackData()

    monkeypatch.setattr(
        "main._build_post_init_container", lambda application: (container, "post_init")
    )

    callback_ran = False

    async def sample_callback(application):
        nonlocal callback_ran
        callback_ran = True
        assert application is app

    register_post_init_hook(app, sample_callback)

    assert getattr(app, "post_init") is container
    assert container.callbacks == [sample_callback]

    asyncio.run(container(app))
    assert callback_ran is True
