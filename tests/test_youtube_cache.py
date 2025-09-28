"""Tests for the persistent YouTube uploads cache."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

from genti.connectors.youtube import YouTubeUploadsCache


@pytest.fixture()
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / "uploads_cache.json"


def test_snapshot_and_get(cache_path: Path) -> None:
    cache = YouTubeUploadsCache(cache_path)
    cache._remember_uploads_playlist("channel", "playlist")

    assert cache.get("channel") == "playlist"
    assert cache.snapshot() == {"channel": "playlist"}
    contents = json.loads(cache_path.read_text(encoding="utf-8"))
    assert contents["version"] == 1
    assert contents["youtube"]["uploads_playlists"] == {"channel": "playlist"}


def test_concurrent_remember_uploads_playlist(cache_path: Path) -> None:
    cache = YouTubeUploadsCache(cache_path)

    def worker(index: int) -> None:
        channel = f"channel-{index % 5}"
        playlist = f"playlist-{index}"
        cache._remember_uploads_playlist(channel, playlist)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, idx) for idx in range(50)]
        for future in futures:
            future.result()

    contents = json.loads(cache_path.read_text(encoding="utf-8"))
    uploads = contents["youtube"]["uploads_playlists"]
    assert set(uploads) <= {f"channel-{i}" for i in range(5)}
    assert all(isinstance(value, str) for value in uploads.values())


def test_legacy_payload_is_loaded(cache_path: Path) -> None:
    cache_path.write_text(json.dumps({"channel": "legacy"}), encoding="utf-8")
    cache = YouTubeUploadsCache(cache_path)

    assert cache.get("channel") == "legacy"
    assert cache.snapshot() == {"channel": "legacy"}
