"""YouTube connector responsible for collecting live/upcoming broadcasts."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Any, Iterable, List, Literal, Sequence, Set, Tuple
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import aiohttp

from genti.models import LiveFeedState, Video


_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_YOUTUBE_SEARCH_COST_UNITS = 100
_YOUTUBE_VIDEO_URL = "https://www.youtube.com/watch?v="


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

    async def fetch(self) -> LiveFeedState:
        if self._http_mode == "async":
            results = await self._fetch_async()
        else:
            results = await self._fetch_sync()

        return self._aggregate_results(results)

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
                errors.add("Не удалось обновить данные с YouTube API.")
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
        live_items, live_error = await asyncio.to_thread(self._search_sync, channel_id, "live")
        upcoming_items: List[dict] = []
        upcoming_error: str | None = None
        if self._show_upcoming:
            upcoming_items, upcoming_error = await asyncio.to_thread(
                self._search_sync,
                channel_id,
                "upcoming",
            )

        errors: List[str] = []
        if live_error:
            errors.append(live_error)
        if upcoming_error:
            errors.append(upcoming_error)

        return self._parse_items(live_items), self._parse_items(upcoming_items), errors

    async def _collect_for_channel(
        self, session: aiohttp.ClientSession, channel_id: str
    ) -> Tuple[List[Video], List[Video], List[str]]:
        live_task = asyncio.create_task(self._search(session, channel_id, "live"))
        upcoming_task = None
        if self._show_upcoming:
            upcoming_task = asyncio.create_task(self._search(session, channel_id, "upcoming"))

        live_items, live_error = await live_task
        upcoming_items: List[dict] = []
        upcoming_error: str | None = None
        if upcoming_task is not None:
            upcoming_items, upcoming_error = await upcoming_task

        errors: List[str] = []
        if live_error:
            errors.append(live_error)
        if upcoming_error:
            errors.append(upcoming_error)

        return self._parse_items(live_items), self._parse_items(upcoming_items), errors

    def _build_params(self, channel_id: str, event_type: str) -> dict[str, Any]:
        return {
            "part": "snippet",
            "channelId": channel_id,
            "eventType": event_type,
            "type": "video",
            "order": "date",
            "maxResults": 10,
            "key": self._api_key,
        }

    async def _search(
        self, session: aiohttp.ClientSession, channel_id: str, event_type: str
    ) -> Tuple[List[dict], str | None]:
        params = self._build_params(channel_id, event_type)
        self._logger.info(
            "Requesting YouTube search: channel=%s type=%s cost=%s units",
            channel_id,
            event_type,
            _YOUTUBE_SEARCH_COST_UNITS,
        )
        try:
            async with session.get(_YOUTUBE_SEARCH_URL, params=params, timeout=20) as response:
                if response.status >= 400:
                    error_detail = await self._extract_error_detail(response)
                    self._logger.error(
                        "YouTube search failed with %s for channel=%s type=%s cost=%s units: %s",
                        response.status,
                        channel_id,
                        event_type,
                        _YOUTUBE_SEARCH_COST_UNITS,
                        error_detail,
                    )
                    return [], self._user_error_message(response.status, error_detail)
                payload = await response.json()
        except asyncio.TimeoutError:
            self._logger.warning(
                "YouTube search timed out: channel=%s type=%s cost=%s units",
                channel_id,
                event_type,
                _YOUTUBE_SEARCH_COST_UNITS,
            )
            return [], "Таймаут запроса к YouTube API."
        except aiohttp.ClientError:
            self._logger.exception(
                "YouTube search failed: channel=%s type=%s cost=%s units",
                channel_id,
                event_type,
                _YOUTUBE_SEARCH_COST_UNITS,
            )
            return [], "Ошибка сети при обращении к YouTube API."

        return self._interpret_payload(channel_id, payload)

    def _search_sync(self, channel_id: str, event_type: str) -> Tuple[List[dict], str | None]:
        params = self._build_params(channel_id, event_type)
        self._logger.info(
            "Requesting YouTube search: channel=%s type=%s cost=%s units",
            channel_id,
            event_type,
            _YOUTUBE_SEARCH_COST_UNITS,
        )
        url = f"{_YOUTUBE_SEARCH_URL}?{urllib_parse.urlencode(params)}"
        request = urllib_request.Request(url)
        try:
            with urllib_request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except urllib_error.HTTPError as exc:
            detail = self._extract_error_detail_sync(exc)
            self._logger.error(
                "YouTube search failed with %s for channel=%s type=%s cost=%s units: %s",
                exc.code,
                channel_id,
                event_type,
                _YOUTUBE_SEARCH_COST_UNITS,
                detail,
            )
            return [], self._user_error_message(exc.code, detail)
        except urllib_error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                self._logger.warning(
                    "YouTube search timed out: channel=%s type=%s cost=%s units",
                    channel_id,
                    event_type,
                    _YOUTUBE_SEARCH_COST_UNITS,
                )
                return [], "Таймаут запроса к YouTube API."
            self._logger.exception(
                "YouTube search failed: channel=%s type=%s cost=%s units",
                channel_id,
                event_type,
                _YOUTUBE_SEARCH_COST_UNITS,
            )
            return [], "Ошибка сети при обращении к YouTube API."
        except (TimeoutError, socket.timeout):
            self._logger.warning(
                "YouTube search timed out: channel=%s type=%s cost=%s units",
                channel_id,
                event_type,
                _YOUTUBE_SEARCH_COST_UNITS,
            )
            return [], "Таймаут запроса к YouTube API."

        return self._interpret_payload(channel_id, payload)

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

    def _interpret_payload(self, channel_id: str, payload: Any) -> Tuple[List[dict], str | None]:
        items: Any = None
        if isinstance(payload, dict):
            items = payload.get("items")
        if not isinstance(items, list):
            self._logger.warning(
                "Unexpected YouTube response structure for channel %s cost=%s units: %s",
                channel_id,
                _YOUTUBE_SEARCH_COST_UNITS,
                payload,
            )
            return [], "Неожиданный ответ YouTube API."
        return items, None

    def _user_error_message(self, status: int, detail: str | None) -> str:
        detail = detail or ""
        detail_lower = detail.lower()
        if status == 403 and "quota" in detail_lower:
            return "Превышена квота YouTube API — обновление временно недоступно."
        if status == 401:
            return "Недействительный ключ YouTube API."
        if detail:
            return f"Ошибка YouTube API: {detail}"
        return f"Ошибка YouTube API (код {status})."

    def _parse_items(self, items: Iterable[dict]) -> List[Video]:
        parsed: List[Video] = []
        for item in items:
            video_id = self._extract_video_id(item)
            if not video_id:
                continue
            snippet = item.get("snippet") or {}
            title = snippet.get("title") or "Без названия"
            channel_title = snippet.get("channelTitle") or "Channel"
            parsed.append(
                Video(
                    video_id=video_id,
                    title=title,
                    channel_title=channel_title,
                    url=f"{_YOUTUBE_VIDEO_URL}{video_id}",
                )
            )
        return parsed

    def _extract_video_id(self, item: dict) -> str | None:
        identifier = item.get("id")
        if isinstance(identifier, dict):
            video_id = identifier.get("videoId")
            if isinstance(video_id, str) and video_id:
                return video_id
        self._logger.debug("Skipping item without videoId: %s", item)
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
