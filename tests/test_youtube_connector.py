import asyncio
import io
import json
from urllib import error as urllib_error

import pytest

pytest.importorskip("aiohttp")

from genti.connectors.youtube import YouTubeLiveConnector
from genti.models import LiveFeedState, Video


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


def test_parse_items_uses_viewer_counts():
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    items = [
        {
            "id": {"videoId": "stream1"},
            "snippet": {"title": "Stream", "channelTitle": "Channel"},
        }
    ]

    videos = connector._parse_items(items, viewer_counts={"stream1": 123})

    assert videos == [
        Video(
            video_id="stream1",
            title="Stream",
            channel_title="Channel",
            url="https://www.youtube.com/watch?v=stream1",
            viewer_count=123,
        )
    ]


def test_extract_viewer_counts_parses_payload():
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    payload = {
        "items": [
            {
                "id": "stream1",
                "liveStreamingDetails": {"concurrentViewers": "42"},
            },
            {
                "id": "stream2",
                "liveStreamingDetails": {"concurrentViewers": 17},
            },
            {
                "id": "stream3",
                "liveStreamingDetails": {},
            },
        ]
    }

    counts = connector._extract_viewer_counts(payload)

    assert counts == {"stream1": 42, "stream2": 17}


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

    items, error_message = await connector._search(ForbiddenSession(), "chan", "live")

    assert items == []
    assert error_message == "Превышена квота YouTube API — обновление временно недоступно."
    assert any(
        "YouTube search failed with 403 for channel=chan type=live: quotaExceeded"
        in record.getMessage()
        for record in caplog.records
    )


def test_youtube_search_sync_handles_forbidden(monkeypatch, caplog):
    connector = YouTubeLiveConnector(api_key="token", channel_ids=["chan"], show_upcoming=False)

    payload = json.dumps(
        {
            "error": {
                "message": "quotaExceeded",
                "errors": [{"reason": "quotaExceeded"}],
            }
        }
    ).encode("utf-8")

    def fake_urlopen(request, timeout):  # noqa: ARG001 - required signature
        raise urllib_error.HTTPError(
            url="https://example.test",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(payload),
        )

    monkeypatch.setattr("genti.connectors.youtube.urllib_request.urlopen", fake_urlopen)

    caplog.set_level("ERROR")

    items, error_message = connector._search_sync("chan", "live")

    assert items == []
    assert error_message == "Превышена квота YouTube API — обновление временно недоступно."
    assert any(
        "YouTube search failed with 403 for channel=chan type=live: quotaExceeded"
        in record.getMessage()
        for record in caplog.records
    )
