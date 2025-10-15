"""YouTube connector responsible for collecting live/upcoming broadcasts."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, Iterable, List, Literal, Sequence, Set, Tuple
from uuid import uuid4
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import aiohttp

from genti.models import LiveFeedState, Video


class YouTubeUploadsCache:
    """Persist uploads playlist identifiers for YouTube channels."""

    def __init__(self, cache_path: Path, *, logger: logging.Logger | None = None) -> None:
        self._cache_path = cache_path
        self._logger = logger or logging.getLogger(__name__)
        self._cache_lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self._loaded = False

    def get(self, channel_id: str) -> str | None:
        self._ensure_loaded()
        with self._cache_lock:
            return self._cache.get(channel_id)

    def snapshot(self) -> dict[str, str]:
        self._ensure_loaded()
        with self._cache_lock:
            return dict(self._cache)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._cache_lock:
            if self._loaded:
                return
            self._cache = self._load_cache_unlocked()
            self._loaded = True

    def _load_cache_unlocked(self) -> dict[str, str]:
        try:
            data = self._cache_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            self._logger.exception("Failed to read YouTube uploads cache", exc_info=True)
            return {}

        try:
            payload = json.loads(data)
        except ValueError:
            self._logger.warning("Invalid YouTube uploads cache contents; ignoring")
            return {}

        if not isinstance(payload, dict):
            return {}

        uploads_payload = payload
        youtube_section = payload.get("youtube")
        if isinstance(youtube_section, dict):
            uploads_payload = youtube_section.get("uploads_playlists") or {}

        cache: dict[str, str] = {}
        if isinstance(uploads_payload, dict):
            for key, value in uploads_payload.items():
                if isinstance(key, str) and isinstance(value, str) and key and value:
                    cache[key] = value
        return cache

    def _remember_uploads_playlist(self, channel_id: str, playlist_id: str) -> None:
        self._ensure_loaded()
        with self._cache_lock:
            existing = self._cache.get(channel_id)
            if existing == playlist_id:
                return
            self._cache[channel_id] = playlist_id
            self._persist_cache_locked()

    def _forget_uploads_playlist(self, channel_id: str) -> None:
        self._ensure_loaded()
        with self._cache_lock:
            if channel_id not in self._cache:
                return
            self._cache.pop(channel_id, None)
            self._persist_cache_locked()

    def _persist_cache_locked(self) -> None:
        cache_dir = self._cache_path.parent
        cache_dir.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            {
                "version": 1,
                "youtube": {"uploads_playlists": self._cache},
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        temp_path = cache_dir / f"{self._cache_path.name}.{uuid4().hex}.tmp"

        try:
            temp_path.write_text(serialized, encoding="utf-8")
            temp_path.replace(self._cache_path)
        finally:
            with suppress(FileNotFoundError):
                temp_path.unlink()


_YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_YOUTUBE_CHANNELS_COST_UNITS = 1
_YOUTUBE_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
_YOUTUBE_PLAYLIST_ITEMS_COST_UNITS = 1
_YOUTUBE_VIDEO_URL = "https://www.youtube.com/watch?v="
_YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_YOUTUBE_VIDEOS_COST_UNITS = 5


class YouTubeLiveConnector:
    """Fetches live and upcoming broadcasts for the configured channels."""

    def __init__(
        self,
        api_key: str,
        channel_ids: Iterable[str],
        *,
        show_upcoming: bool = True,
        session_timeout: float = 30.0,
        http_mode: Literal["sync", "async"] = "sync",
        logger: logging.Logger | None = None,
        uploads_cache_path: str | Path | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("YouTube API key must be provided")
        self._api_key = api_key
        self._channel_ids = list(channel_ids)
        if not self._channel_ids:
            raise ValueError("At least one YouTube channel identifier must be configured")
        self._show_upcoming = show_upcoming
        self._session_timeout = session_timeout
        if http_mode not in {"sync", "async"}:
            raise ValueError("http_mode must be 'sync' or 'async'")
        self._http_mode = http_mode
        self._logger = logger or logging.getLogger(__name__)
        self._uploads_cache: dict[str, str] = {}
        self._uploads_cache_storage: YouTubeUploadsCache | None = None
        if uploads_cache_path is not None:
            cache_path = uploads_cache_path if isinstance(uploads_cache_path, Path) else Path(uploads_cache_path)
            self._uploads_cache_storage = YouTubeUploadsCache(cache_path, logger=self._logger)
            self._uploads_cache.update(self._uploads_cache_storage.snapshot())

    async def fetch(self) -> LiveFeedState:
        if self._http_mode == "async":
            results = await self._fetch_async()
        else:
            results = await self._fetch_sync()

        return self._aggregate_results(results)

    def _remember_uploads_playlist(self, channel_id: str, playlist_id: str) -> None:
        self._uploads_cache[channel_id] = playlist_id
        if self._uploads_cache_storage is not None:
            self._uploads_cache_storage._remember_uploads_playlist(channel_id, playlist_id)

    def _forget_uploads_playlist(self, channel_id: str) -> None:
        self._uploads_cache.pop(channel_id, None)
        if self._uploads_cache_storage is not None:
            self._uploads_cache_storage._forget_uploads_playlist(channel_id)

    async def _fetch_async(self) -> Sequence[Tuple[List[Video], List[Video], List[str]] | Exception]:
        timeout = aiohttp.ClientTimeout(total=self._session_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [self._collect_for_channel(session, channel_id) for channel_id in self._channel_ids]
            return await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_sync(self) -> Sequence[Tuple[List[Video], List[Video], List[str]] | Exception]:
        tasks = [self._collect_for_channel_sync(channel_id) for channel_id in self._channel_ids]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def _aggregate_results(
        self,
        results: Sequence[Tuple[List[Video], List[Video], List[str]] | Exception],
    ) -> LiveFeedState:
        live_entries: List[Video] = []
        upcoming_entries: List[Video] = []
        errors: Set[str] = set()

        for channel_id, result in zip(self._channel_ids, results):
            if isinstance(result, Exception):
                self._logger.warning("Failed to fetch channel %s", channel_id, exc_info=result)
                errors.add("Unable to refresh data from the YouTube API.")
                continue
            live, upcoming, channel_errors = result
            live_entries.extend(live)
            upcoming_entries.extend(upcoming)
            errors.update(channel_errors)

        return LiveFeedState(
            live=self._deduplicate(live_entries),
            upcoming=self._deduplicate(upcoming_entries),
            errors=sorted(errors),
        )

    async def _collect_for_channel_sync(self, channel_id: str) -> Tuple[List[Video], List[Video], List[str]]:
        playlist_id, playlist_error = await asyncio.to_thread(self._ensure_uploads_playlist_sync, channel_id)
        errors: List[str] = []
        if playlist_error:
            errors.append(playlist_error)
        if not playlist_id:
            return [], [], errors

        video_ids, playlist_items_error, playlist_invalid = await asyncio.to_thread(
            self._playlist_video_ids_sync,
            channel_id,
            playlist_id,
        )
        if playlist_invalid:
            refreshed_playlist_id, refresh_error = await asyncio.to_thread(
                self._ensure_uploads_playlist_sync,
                channel_id,
            )
            if refresh_error:
                errors.append(refresh_error)
                return [], [], errors
            if not refreshed_playlist_id:
                self._logger.warning(
                    "Failed to refresh uploads playlist for channel %s after invalidation",
                    channel_id,
                )
                return [], [], errors
            playlist_id = refreshed_playlist_id
            video_ids, playlist_items_error, playlist_invalid = await asyncio.to_thread(
                self._playlist_video_ids_sync,
                channel_id,
                playlist_id,
            )
        if playlist_items_error:
            errors.append(playlist_items_error)
        if playlist_invalid or not video_ids:
            return [], [], errors

        video_items, video_error = await asyncio.to_thread(
            self._fetch_video_metadata_sync,
            video_ids,
        )
        if video_error:
            errors.append(video_error)

        live, upcoming = self._classify_videos(channel_id, video_items)
        return live, upcoming, errors

    async def _collect_for_channel(
        self, session: aiohttp.ClientSession, channel_id: str
    ) -> Tuple[List[Video], List[Video], List[str]]:
        playlist_id, playlist_error = await self._ensure_uploads_playlist_async(session, channel_id)
        errors: List[str] = []
        if playlist_error:
            errors.append(playlist_error)
        if not playlist_id:
            return [], [], errors

        video_ids, playlist_items_error, playlist_invalid = await self._playlist_video_ids_async(
            session,
            channel_id,
            playlist_id,
        )
        if playlist_invalid:
            refreshed_playlist_id, refresh_error = await self._ensure_uploads_playlist_async(session, channel_id)
            if refresh_error:
                errors.append(refresh_error)
                return [], [], errors
            if not refreshed_playlist_id:
                self._logger.warning(
                    "Failed to refresh uploads playlist for channel %s after invalidation",
                    channel_id,
                )
                return [], [], errors
            playlist_id = refreshed_playlist_id
            video_ids, playlist_items_error, playlist_invalid = await self._playlist_video_ids_async(
                session,
                channel_id,
                playlist_id,
            )
        if playlist_items_error:
            errors.append(playlist_items_error)
        if playlist_invalid or not video_ids:
            return [], [], errors

        video_items, video_error = await self._fetch_video_metadata_async(session, video_ids)
        if video_error:
            errors.append(video_error)

        live, upcoming = self._classify_videos(channel_id, video_items)
        return live, upcoming, errors

    async def _ensure_uploads_playlist_async(
        self, session: aiohttp.ClientSession, channel_id: str
    ) -> Tuple[str | None, str | None]:
        cached = self._uploads_cache.get(channel_id)
        if cached:
            return cached, None

        params = {
            "part": "contentDetails",
            "id": channel_id,
            "key": self._api_key,
        }
        self._logger.info(
            "Requesting YouTube channels: channel=%s cost=%s units",
            channel_id,
            _YOUTUBE_CHANNELS_COST_UNITS,
        )
        try:
            async with session.get(_YOUTUBE_CHANNELS_URL, params=params, timeout=20) as response:
                if response.status >= 400:
                    detail = await self._extract_error_detail(response)
                    self._logger.error(
                        "YouTube channels failed with %s for channel=%s cost=%s units: %s",
                        response.status,
                        channel_id,
                        _YOUTUBE_CHANNELS_COST_UNITS,
                        detail,
                    )
                    return None, self._user_error_message(response.status, detail)
                payload = await response.json()
        except asyncio.TimeoutError:
            self._logger.warning(
                "YouTube channels timed out: channel=%s cost=%s units",
                channel_id,
                _YOUTUBE_CHANNELS_COST_UNITS,
            )
            return None, "YouTube API request timed out."
        except aiohttp.ClientError:
            self._logger.exception(
                "YouTube channels failed: channel=%s cost=%s units",
                channel_id,
                _YOUTUBE_CHANNELS_COST_UNITS,
            )
            return None, "Network error while contacting the YouTube API."

        playlist_id = self._extract_uploads_playlist(payload)
        if not playlist_id:
            self._logger.warning(
                "YouTube channels returned no uploads playlist for channel=%s cost=%s units",
                channel_id,
                _YOUTUBE_CHANNELS_COST_UNITS,
            )
            return None, None

        self._remember_uploads_playlist(channel_id, playlist_id)
        return playlist_id, None

    def _ensure_uploads_playlist_sync(self, channel_id: str) -> Tuple[str | None, str | None]:
        cached = self._uploads_cache.get(channel_id)
        if cached:
            return cached, None

        params = {
            "part": "contentDetails",
            "id": channel_id,
            "key": self._api_key,
        }
        self._logger.info(
            "Requesting YouTube channels: channel=%s cost=%s units",
            channel_id,
            _YOUTUBE_CHANNELS_COST_UNITS,
        )
        url = f"{_YOUTUBE_CHANNELS_URL}?{urllib_parse.urlencode(params)}"
        request = urllib_request.Request(url)
        try:
            with urllib_request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except urllib_error.HTTPError as exc:
            detail = self._extract_error_detail_sync(exc)
            self._logger.error(
                "YouTube channels failed with %s for channel=%s cost=%s units: %s",
                exc.code,
                channel_id,
                _YOUTUBE_CHANNELS_COST_UNITS,
                detail,
            )
            return None, self._user_error_message(exc.code, detail)
        except urllib_error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                self._logger.warning(
                    "YouTube channels timed out: channel=%s cost=%s units",
                    channel_id,
                    _YOUTUBE_CHANNELS_COST_UNITS,
                )
                return None, "YouTube API request timed out."
            self._logger.exception(
                "YouTube channels failed: channel=%s cost=%s units",
                channel_id,
                _YOUTUBE_CHANNELS_COST_UNITS,
            )
            return None, "Network error while contacting the YouTube API."
        except (TimeoutError, socket.timeout):
            self._logger.warning(
                "YouTube channels timed out: channel=%s cost=%s units",
                channel_id,
                _YOUTUBE_CHANNELS_COST_UNITS,
            )
            return None, "YouTube API request timed out."

        playlist_id = self._extract_uploads_playlist(payload)
        if not playlist_id:
            self._logger.warning(
                "YouTube channels returned no uploads playlist for channel=%s cost=%s units",
                channel_id,
                _YOUTUBE_CHANNELS_COST_UNITS,
            )
            return None, None

        self._remember_uploads_playlist(channel_id, playlist_id)
        return playlist_id, None

    async def _playlist_video_ids_async(
        self, session: aiohttp.ClientSession, channel_id: str, playlist_id: str
    ) -> Tuple[List[str], str | None, bool]:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 10,
            "key": self._api_key,
        }
        self._logger.info(
            "Requesting YouTube playlistItems: playlist=%s cost=%s units",
            playlist_id,
            _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
        )
        try:
            async with session.get(_YOUTUBE_PLAYLIST_ITEMS_URL, params=params, timeout=20) as response:
                if response.status >= 400:
                    detail = await self._extract_error_detail(response)
                    self._logger.error(
                        "YouTube playlistItems failed with %s for playlist=%s cost=%s units: %s",
                        response.status,
                        playlist_id,
                        _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
                        detail,
                    )
                    playlist_invalid = self._handle_possible_playlist_invalidation(
                        channel_id,
                        response.status,
                        detail,
                    )
                    return [], self._user_error_message(response.status, detail), playlist_invalid
                payload = await response.json()
        except asyncio.TimeoutError:
            self._logger.warning(
                "YouTube playlistItems timed out: playlist=%s cost=%s units",
                playlist_id,
                _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
            )
            return [], "YouTube API request timed out.", False
        except aiohttp.ClientError:
            self._logger.exception(
                "YouTube playlistItems failed: playlist=%s cost=%s units",
                playlist_id,
                _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
            )
            return [], "Network error while contacting the YouTube API.", False

        return self._extract_playlist_video_ids(payload), None, False

    def _playlist_video_ids_sync(
        self, channel_id: str, playlist_id: str
    ) -> Tuple[List[str], str | None, bool]:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 10,
            "key": self._api_key,
        }
        self._logger.info(
            "Requesting YouTube playlistItems: playlist=%s cost=%s units",
            playlist_id,
            _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
        )
        url = f"{_YOUTUBE_PLAYLIST_ITEMS_URL}?{urllib_parse.urlencode(params)}"
        request = urllib_request.Request(url)
        try:
            with urllib_request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except urllib_error.HTTPError as exc:
            detail = self._extract_error_detail_sync(exc)
            self._logger.error(
                "YouTube playlistItems failed with %s for playlist=%s cost=%s units: %s",
                exc.code,
                playlist_id,
                _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
                detail,
            )
            playlist_invalid = self._handle_possible_playlist_invalidation(
                channel_id,
                exc.code,
                detail,
            )
            return [], self._user_error_message(exc.code, detail), playlist_invalid
        except urllib_error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                self._logger.warning(
                    "YouTube playlistItems timed out: playlist=%s cost=%s units",
                    playlist_id,
                    _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
                )
                return [], "YouTube API request timed out.", False
            self._logger.exception(
                "YouTube playlistItems failed: playlist=%s cost=%s units",
                playlist_id,
                _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
            )
            return [], "Network error while contacting the YouTube API.", False
        except (TimeoutError, socket.timeout):
            self._logger.warning(
                "YouTube playlistItems timed out: playlist=%s cost=%s units",
                playlist_id,
                _YOUTUBE_PLAYLIST_ITEMS_COST_UNITS,
            )
            return [], "YouTube API request timed out.", False

        return self._extract_playlist_video_ids(payload), None, False

    def _handle_possible_playlist_invalidation(
        self, channel_id: str, status: int, detail: str | None
    ) -> bool:
        if self._is_playlist_not_found_error(status, detail):
            self._logger.info(
                "Uploads playlist invalidated for channel=%s; clearing cache", channel_id
            )
            self._forget_uploads_playlist(channel_id)
            return True
        return False

    def _is_playlist_not_found_error(self, status: int, detail: str | None) -> bool:
        if status == 404:
            return True
        if detail:
            detail_lower = detail.lower()
            if "playlist" in detail_lower and "cannot be found" in detail_lower:
                return True
            if "playlistnotfound" in detail_lower:
                return True
        return False

    async def _fetch_video_metadata_async(
        self, session: aiohttp.ClientSession, video_ids: Sequence[str]
    ) -> Tuple[List[dict], str | None]:
        items: List[dict] = []
        error_message: str | None = None
        for chunk in self._chunk_ids(video_ids):
            params = self._build_videos_params(chunk)
            self._logger.info(
                "Requesting YouTube videos: ids=%s cost=%s units",
                ",".join(chunk),
                _YOUTUBE_VIDEOS_COST_UNITS,
            )
            try:
                async with session.get(_YOUTUBE_VIDEOS_URL, params=params, timeout=20) as response:
                    if response.status >= 400:
                        detail = await self._extract_error_detail(response)
                        self._logger.warning(
                            "YouTube videos failed with %s for ids=%s cost=%s units: %s",
                            response.status,
                            ",".join(chunk),
                            _YOUTUBE_VIDEOS_COST_UNITS,
                            detail,
                        )
                        error_message = error_message or self._user_error_message(response.status, detail)
                        continue
                    payload = await response.json()
            except asyncio.TimeoutError:
                self._logger.warning(
                    "YouTube videos timed out: ids=%s cost=%s units",
                    ",".join(chunk),
                    _YOUTUBE_VIDEOS_COST_UNITS,
                )
                error_message = error_message or "YouTube API request timed out."
                continue
            except aiohttp.ClientError:
                self._logger.exception(
                    "YouTube videos failed: ids=%s cost=%s units",
                    ",".join(chunk),
                    _YOUTUBE_VIDEOS_COST_UNITS,
                )
                error_message = error_message or "Network error while contacting the YouTube API."
                continue

            items.extend(self._extract_video_items(payload))

        return items, error_message

    def _fetch_video_metadata_sync(self, video_ids: Sequence[str]) -> Tuple[List[dict], str | None]:
        items: List[dict] = []
        error_message: str | None = None
        for chunk in self._chunk_ids(video_ids):
            params = self._build_videos_params(chunk)
            self._logger.info(
                "Requesting YouTube videos: ids=%s cost=%s units",
                ",".join(chunk),
                _YOUTUBE_VIDEOS_COST_UNITS,
            )
            url = f"{_YOUTUBE_VIDEOS_URL}?{urllib_parse.urlencode(params)}"
            request = urllib_request.Request(url)
            try:
                with urllib_request.urlopen(request, timeout=20) as response:
                    payload = json.load(response)
            except urllib_error.HTTPError as exc:
                detail = self._extract_error_detail_sync(exc)
                self._logger.warning(
                    "YouTube videos failed with %s for ids=%s cost=%s units: %s",
                    exc.code,
                    ",".join(chunk),
                    _YOUTUBE_VIDEOS_COST_UNITS,
                    detail,
                )
                error_message = error_message or self._user_error_message(exc.code, detail)
                continue
            except urllib_error.URLError as exc:
                reason = exc.reason
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    self._logger.warning(
                        "YouTube videos timed out: ids=%s cost=%s units",
                        ",".join(chunk),
                        _YOUTUBE_VIDEOS_COST_UNITS,
                    )
                    error_message = error_message or "YouTube API request timed out."
                else:
                    self._logger.exception(
                        "YouTube videos failed: ids=%s cost=%s units",
                        ",".join(chunk),
                        _YOUTUBE_VIDEOS_COST_UNITS,
                    )
                    error_message = error_message or "Network error while contacting the YouTube API."
                continue
            except (TimeoutError, socket.timeout):
                self._logger.warning(
                    "YouTube videos timed out: ids=%s cost=%s units",
                    ",".join(chunk),
                    _YOUTUBE_VIDEOS_COST_UNITS,
                )
                error_message = error_message or "YouTube API request timed out."
                continue

            items.extend(self._extract_video_items(payload))

        return items, error_message

    async def _extract_error_detail(self, response: aiohttp.ClientResponse) -> str:
        try:
            payload: Any = await response.json()
        except (aiohttp.ContentTypeError, ValueError):
            text = (await response.text())[:200]
            if text:
                return text
            return response.reason or str(response.status)

        detail = self._error_detail_from_payload(payload)
        if detail:
            return detail
        return response.reason or str(response.status)

    def _extract_error_detail_sync(self, error: urllib_error.HTTPError) -> str:
        try:
            data = error.read()
        except Exception:  # pragma: no cover - extremely defensive
            data = b""

        if data:
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                text = data.decode("utf-8", errors="ignore").strip()
                if text:
                    return text[:200]
            else:
                detail = self._error_detail_from_payload(payload)
                if detail:
                    return detail

        return error.reason or str(error.code)

    def _error_detail_from_payload(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message:
                    return message
                errors = error.get("errors")
                if isinstance(errors, list) and errors:
                    first = errors[0]
                    if isinstance(first, dict):
                        reason = first.get("reason")
                        if isinstance(reason, str) and reason:
                            return reason
        return None

    def _user_error_message(self, status: int, detail: str | None) -> str:
        detail = detail or ""
        detail_lower = detail.lower()
        if status == 403 and "quota" in detail_lower:
            return "YouTube API quota exceeded—updates are temporarily unavailable."
        if status == 401:
            return "Invalid YouTube API key."
        if detail:
            return f"YouTube API error: {detail}"
        return f"YouTube API error (status {status})."

    def _build_videos_params(self, video_ids: Sequence[str]) -> dict[str, Any]:
        return {
            "part": "snippet,liveStreamingDetails",
            "id": ",".join(video_ids),
            "key": self._api_key,
        }

    def _chunk_ids(self, video_ids: Sequence[str], chunk_size: int = 50) -> List[List[str]]:
        return [list(video_ids[i : i + chunk_size]) for i in range(0, len(video_ids), chunk_size)]

    def _extract_video_items(self, payload: Any) -> List[dict]:
        if not isinstance(payload, dict):
            return []
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def _extract_uploads_playlist(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        if not isinstance(first, dict):
            return None
        details = first.get("contentDetails")
        if not isinstance(details, dict):
            return None
        related = details.get("relatedPlaylists")
        if not isinstance(related, dict):
            return None
        uploads = related.get("uploads")
        if isinstance(uploads, str) and uploads:
            return uploads
        return None

    def _extract_playlist_video_ids(self, payload: Any) -> List[str]:
        video_ids: List[str] = []
        if not isinstance(payload, dict):
            return video_ids
        items = payload.get("items")
        if not isinstance(items, list):
            return video_ids
        for item in items:
            if not isinstance(item, dict):
                continue
            details = item.get("contentDetails")
            if not isinstance(details, dict):
                continue
            video_id = details.get("videoId")
            if isinstance(video_id, str) and video_id:
                video_ids.append(video_id)
        return video_ids

    def _classify_videos(
        self, channel_id: str, items: Iterable[dict]
    ) -> Tuple[List[Video], List[Video]]:
        live: List[Video] = []
        upcoming: List[Video] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("id")
            if not isinstance(video_id, str) or not video_id:
                continue
            snippet = item.get("snippet")
            if not isinstance(snippet, dict):
                continue
            snippet_channel_id = snippet.get("channelId")
            if isinstance(snippet_channel_id, str) and snippet_channel_id and snippet_channel_id != channel_id:
                continue
            status = snippet.get("liveBroadcastContent")
            if status not in {"live", "upcoming"}:
                continue
            title = snippet.get("title") or "Untitled"
            channel_title = snippet.get("channelTitle") or "Channel"
            viewer_count = self._extract_viewer_count(item)
            video = Video(
                video_id=video_id,
                title=title,
                channel_title=channel_title,
                url=f"{_YOUTUBE_VIDEO_URL}{video_id}",
                viewer_count=viewer_count,
            )
            if status == "live":
                live.append(video)
            elif self._show_upcoming:
                upcoming.append(video)
        return live, upcoming

    def _extract_viewer_count(self, item: Any) -> int | None:
        if not isinstance(item, dict):
            return None
        details = item.get("liveStreamingDetails")
        if not isinstance(details, dict):
            return None
        viewers = details.get("concurrentViewers")
        if isinstance(viewers, str):
            try:
                return int(viewers)
            except ValueError:
                return None
        if isinstance(viewers, int):
            return viewers
        return None

    def _deduplicate(self, entries: Iterable[Video]) -> List[Video]:
        seen: set[str] = set()
        unique: List[Video] = []
        for entry in entries:
            if entry.video_id in seen:
                continue
            seen.add(entry.video_id)
            unique.append(entry)
        return unique
