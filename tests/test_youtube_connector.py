import asyncio

import pytest

pytest.importorskip("aiohttp")

from genti.connectors.youtube import YouTubeLiveConnector
from genti.models import LiveFeedState, Video


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_youtube_connector_deduplicates(monkeypatch):
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=True)

    async def fake_collect(self, session, channel_id):
        return (
            [
                Video("v1", "A", "C", "url1"),
                Video("v1", "A duplicate", "C", "url1"),
            ],
            [Video("v2", "B", "C", "url2")],
        )

    monkeypatch.setattr(
        YouTubeLiveConnector,
        "_collect_for_channel",
        fake_collect,
    )
    monkeypatch.setattr(
        "genti.connectors.youtube.aiohttp.ClientSession",
        lambda timeout=None: DummySession(),
    )

    async def run_fetch():
        return await connector.fetch()

    state = asyncio.run(run_fetch())
    assert isinstance(state, LiveFeedState)
    assert [video.video_id for video in state.live] == ["v1"]
    assert [video.video_id for video in state.upcoming] == ["v2"]
