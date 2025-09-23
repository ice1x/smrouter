import html
import sys
import types
from pathlib import Path
import importlib.util

import pytest


@pytest.fixture(scope="module")
def formatting_module():
    """Import ``main`` with telegram/aiohttp stubs so formatting helpers are available."""

    # Prepare lightweight stubs for optional dependencies used only for typing/parse mode.
    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ClientSession = object
    aiohttp_stub.ClientTimeout = object

    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Message = object
    telegram_stub.Chat = object
    telegram_stub.constants = types.SimpleNamespace(
        ParseMode=types.SimpleNamespace(HTML="HTML")
    )

    telegram_ext_stub = types.ModuleType("telegram.ext")
    telegram_ext_stub.Application = object
    telegram_ext_stub.ApplicationBuilder = object
    telegram_ext_stub.CommandHandler = object

    stubs = {
        "aiohttp": aiohttp_stub,
        "telegram": telegram_stub,
        "telegram.ext": telegram_ext_stub,
    }

    original_main = sys.modules.pop("main", None)
    originals: dict[str, types.ModuleType | None] = {
        name: sys.modules.get(name) for name in stubs
    }

    try:
        sys.modules.update(stubs)
        module_name = "main"
        module_path = Path(__file__).resolve().parent.parent / "main.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        if original_main is not None:
            sys.modules["main"] = original_main
        else:
            sys.modules.pop("main", None)

        for name, previous in originals.items():
            if previous is not None:
                sys.modules[name] = previous
            else:
                sys.modules.pop(name, None)


def _sample_item(video_id: str, title: str, channel_title: str) -> dict:
    return {
        "id": {"videoId": video_id},
        "snippet": {"title": title, "channelTitle": channel_title},
    }


def test_build_dashboard_text_escapes_html_sensitive_characters(formatting_module):
    build_dashboard_text = formatting_module.build_dashboard_text
    youtube_vid_url = formatting_module.YOUTUBE_VID_URL
    special = "_*[]()~`>#+-=|{}.!&<>\"'"
    state = {
        "live": [
            _sample_item(
                "live1",
                special,
                "Channel & Co <Live>",
            )
        ],
        "upcoming": [
            _sample_item(
                "up1",
                "Upcoming " + special,
                "Future_Channel",
            )
        ],
    }

    text = build_dashboard_text(state)

    assert html.escape(special, quote=True) in text
    assert html.escape("Channel & Co <Live>", quote=True) in text

    url = f"{youtube_vid_url}live1"
    assert html.escape(url, quote=True) in text


def test_build_live_notification_text_escapes_values(formatting_module):
    build_live_notification_text = formatting_module.build_live_notification_text
    title = "Special _title_ with <> &"
    channel = "Channel " + title
    url = "https://youtu.be/vid?arg=1&other=_value_"

    text = build_live_notification_text(title, channel, url)

    assert "<b>LIVE</b>" in text
    assert html.escape(title, quote=True) in text
    assert html.escape(channel, quote=True) in text
    assert html.escape(url, quote=True) in text
