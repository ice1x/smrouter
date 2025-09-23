import html
import sys
import types
from pathlib import Path

# Stub external dependencies that are not required for formatting tests.
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = object
sys.modules.setdefault("aiohttp", aiohttp_stub)

telegram_stub = types.ModuleType("telegram")
telegram_stub.Message = object
telegram_stub.Chat = object

constants_stub = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
telegram_stub.constants = constants_stub
sys.modules.setdefault("telegram", telegram_stub)

telegram_ext_stub = types.ModuleType("telegram.ext")
telegram_ext_stub.Application = object
telegram_ext_stub.ApplicationBuilder = object
telegram_ext_stub.CommandHandler = object
sys.modules.setdefault("telegram.ext", telegram_ext_stub)

sys.path.append(str(Path(__file__).resolve().parent.parent))

from main import build_dashboard_text, build_live_notification_text, YOUTUBE_VID_URL


def _sample_item(video_id: str, title: str, channel_title: str) -> dict:
    return {
        "id": {"videoId": video_id},
        "snippet": {"title": title, "channelTitle": channel_title},
    }


def test_build_dashboard_text_escapes_html_sensitive_characters():
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

    url = f"{YOUTUBE_VID_URL}live1"
    assert html.escape(url, quote=True) in text


def test_build_live_notification_text_escapes_values():
    title = "Special _title_ with <> &"
    channel = "Channel " + title
    url = "https://youtu.be/vid?arg=1&other=_value_"

    text = build_live_notification_text(title, channel, url)

    assert "<b>LIVE</b>" in text
    assert html.escape(title, quote=True) in text
    assert html.escape(channel, quote=True) in text
    assert html.escape(url, quote=True) in text
