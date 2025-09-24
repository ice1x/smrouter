import pytest

pytest.importorskip("aiohttp")

from main import register_post_init_hook

telegram_ext = pytest.importorskip("telegram.ext")
ApplicationBuilder = telegram_ext.ApplicationBuilder


def test_register_post_init_hook_handles_missing_container():
    app = ApplicationBuilder().token("123:TESTTOKEN").build()
    app._post_init = None

    async def sample_callback(application):
        return None

    register_post_init_hook(app, sample_callback)
