import json

import pytest

pytest.importorskip("aiohttp")

from genti.connectors.youtube import YouTubeLiveConnector


def test_connector_loads_cache(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps({"schema": 1, "uploads_playlists": {"chan": "uploads"}}),
        encoding="utf-8",
    )

    connector = YouTubeLiveConnector(
        api_key="token",
        channel_ids=["chan"],
        show_upcoming=False,
        cache_path=cache_path,
    )

    assert connector._uploads_cache == {"chan": "uploads"}


def test_connector_persists_cache(tmp_path):
    cache_path = tmp_path / "cache.json"
    connector = YouTubeLiveConnector(
        api_key="token",
        channel_ids=["chan"],
        show_upcoming=False,
        cache_path=cache_path,
    )

    connector._remember_uploads_playlist("chan", "uploads")

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload == {"schema": 1, "uploads_playlists": {"chan": "uploads"}}
