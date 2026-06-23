import asyncio
import io
import json
import socket
from urllib import error as urllib_error

import pytest

pytest.importorskip("aiohttp")

from src.connectors import youtube as youtube_module
from src.connectors.youtube import YouTubeLiveConnector
from src.models import LiveFeedState, Video


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, *args, **kwargs):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _videos_payload(video_id):
    return {
        "items": [
            {
                "id": video_id,
                "snippet": {"title": "T", "channelTitle": "C", "liveBroadcastContent": "none"},
            }
        ]
    }


def test_youtube_connector_deduplicates(monkeypatch):
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=True)

    async def fake_collect_sync(self, channel_id):
        return (
            [
                Video("v1", "A", "C", "url1"),
                Video("v1", "A duplicate", "C", "url1"),
            ],
            [Video("v2", "B", "C", "url2")],
            [],
        )

    monkeypatch.setattr(YouTubeLiveConnector, "_collect_for_channel_sync", fake_collect_sync)

    async def run_fetch():
        return await connector.fetch()

    state = asyncio.run(run_fetch())
    assert isinstance(state, LiveFeedState)
    assert [video.video_id for video in state.live] == ["v1"]
    assert [video.video_id for video in state.upcoming] == ["v2"]


def test_playlist_not_found_message_not_surfaced():
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    errors = {
        "Network error while contacting the YouTube API.",
        "Uploads playlist not found—check the channel ID or privacy settings.",
    }

    summarized = connector._summarize_errors(errors)

    assert summarized == [
        "Cannot reach the YouTube API right now—will retry automatically.",
    ]


def test_video_metadata_sync_retries_once_then_succeeds(monkeypatch):
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib_error.URLError(socket.gaierror(8, "nodename nor servname provided"))
        return _FakeResponse(_videos_payload("v1"))

    monkeypatch.setattr(youtube_module.urllib_request, "urlopen", fake_urlopen)

    items, error = connector._fetch_video_metadata_sync(["v1"])

    assert calls["n"] == 2  # initial attempt + one retry
    assert error is None
    assert [item["id"] for item in items] == ["v1"]


def test_video_metadata_sync_gives_up_after_one_retry(monkeypatch):
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        raise urllib_error.URLError(socket.gaierror(8, "nodename nor servname provided"))

    monkeypatch.setattr(youtube_module.urllib_request, "urlopen", fake_urlopen)

    items, error = connector._fetch_video_metadata_sync(["v1"])

    assert calls["n"] == 2  # initial attempt + exactly one retry, then give up
    assert items == []
    # DNS failures get a specific, more actionable message...
    assert "resolve" in error.lower()
    # ...that still collapses into the transient retry summary for users.
    assert connector._summarize_errors({error}) == [
        "Cannot reach the YouTube API right now—will retry automatically."
    ]


def test_video_metadata_sync_does_not_retry_http_errors(monkeypatch):
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        raise urllib_error.HTTPError(
            url="https://example.test",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(youtube_module.urllib_request, "urlopen", fake_urlopen)

    items, error = connector._fetch_video_metadata_sync(["v1"])

    assert calls["n"] == 1  # HTTP errors are not transient; no retry
    assert items == []
    assert error == "Invalid YouTube API key."
