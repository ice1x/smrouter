import asyncio
import io
import json
from urllib import error as urllib_error

import pytest

pytest.importorskip("aiohttp")

from src.connectors.youtube import YouTubeLiveConnector
from src.models import LiveFeedState, Video


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
