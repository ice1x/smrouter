import asyncio
import sys
import types

if "aiohttp" not in sys.modules:
    aiohttp_stub = types.SimpleNamespace(
        ClientSession=None,
        ClientTimeout=types.SimpleNamespace,
        ClientError=Exception,
    )
    sys.modules["aiohttp"] = aiohttp_stub

from genti.connectors.youtube import YouTubeLiveConnector


def test_playlist_invalidation_refreshes_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "yt_cache.json"
    connector = YouTubeLiveConnector(
        api_key="token",
        channel_ids=["chan"],
        show_upcoming=False,
        uploads_cache_path=cache_path,
    )

    connector._remember_uploads_playlist("chan", "cached")

    playlist_calls = []

    def fake_playlist_video_ids_sync(self, channel_id, playlist_id):
        playlist_calls.append((channel_id, playlist_id))
        if len(playlist_calls) == 1:
            return [], "YouTube API error: playlistNotFound", True
        return ["video1"], None, False

    ensure_calls = []

    def fake_ensure_uploads_playlist_sync(self, channel_id):
        ensure_calls.append(channel_id)
        if len(ensure_calls) == 1:
            return "cached", None
        self._remember_uploads_playlist(channel_id, "fresh")
        return "fresh", None

    def fake_fetch_video_metadata_sync(self, video_ids):
        return [], None

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        YouTubeLiveConnector,
        "_playlist_video_ids_sync",
        fake_playlist_video_ids_sync,
    )
    monkeypatch.setattr(
        YouTubeLiveConnector,
        "_ensure_uploads_playlist_sync",
        fake_ensure_uploads_playlist_sync,
    )
    monkeypatch.setattr(
        YouTubeLiveConnector,
        "_fetch_video_metadata_sync",
        fake_fetch_video_metadata_sync,
    )

    live, upcoming, errors = asyncio.run(connector._collect_for_channel_sync("chan"))

    assert live == []
    assert upcoming == []
    assert errors == []
    assert playlist_calls == [("chan", "cached"), ("chan", "fresh")]
    assert ensure_calls == ["chan", "chan"]
    assert connector._uploads_cache["chan"] == "fresh"
    assert connector._uploads_cache_storage is not None
    assert connector._uploads_cache_storage.snapshot()["chan"] == "fresh"
