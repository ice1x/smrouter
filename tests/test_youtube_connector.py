import asyncio

import pytest

pytest.importorskip("aiohttp")
from yarl import URL


import aiohttp

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


class ForbiddenResponse:
    def __init__(self):
        self.status = 403
        self.reason = "Forbidden"
        self.url = "https://example.test"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return {
            "error": {
                "message": "quotaExceeded",
                "errors": [
                    {
                        "reason": "quotaExceeded",
                    }
                ],
            }
        }


class ForbiddenSession:
    def get(self, *args, **kwargs):
        return ForbiddenResponse()


@pytest.mark.asyncio
async def test_youtube_search_handles_forbidden(caplog):
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    caplog.set_level("ERROR")

    result = await connector._search(ForbiddenSession(), "chan", "live")

    assert result == []
    assert any(
        "YouTube search failed with 403 for channel=chan type=live: quotaExceeded"
        in record.getMessage()
        for record in caplog.records
    )
