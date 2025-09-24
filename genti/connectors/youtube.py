"""YouTube connector responsible for collecting live/upcoming broadcasts."""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable, List, Tuple

import aiohttp

from genti.models import LiveFeedState, Video


_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
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
        self._logger = logger or logging.getLogger(__name__)

    async def fetch(self) -> LiveFeedState:
        timeout = aiohttp.ClientTimeout(total=self._session_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [self._collect_for_channel(session, channel_id) for channel_id in self._channel_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        live_entries: List[Video] = []
        upcoming_entries: List[Video] = []

        for channel_id, result in zip(self._channel_ids, results):
            if isinstance(result, Exception):
                self._logger.warning("Failed to fetch channel %s", channel_id, exc_info=result)
                continue
            live, upcoming = result
            live_entries.extend(live)
            upcoming_entries.extend(upcoming)

        return LiveFeedState(
            live=self._deduplicate(live_entries),
            upcoming=self._deduplicate(upcoming_entries),
        )

    async def _collect_for_channel(
        self, session: aiohttp.ClientSession, channel_id: str
    ) -> Tuple[List[Video], List[Video]]:
        live_task = asyncio.create_task(self._search(session, channel_id, "live"))
        upcoming_task = None
        if self._show_upcoming:
            upcoming_task = asyncio.create_task(self._search(session, channel_id, "upcoming"))

        live_items = await live_task
        upcoming_items = []
        if upcoming_task is not None:
            upcoming_items = await upcoming_task

        return self._parse_items(live_items), self._parse_items(upcoming_items)

    async def _search(
        self, session: aiohttp.ClientSession, channel_id: str, event_type: str
    ) -> List[dict]:
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "eventType": event_type,
            "type": "video",
            "order": "date",
            "maxResults": 10,
            "key": self._api_key,
        }
        self._logger.debug("Requesting YouTube search: channel=%s type=%s", channel_id, event_type)
        try:
            async with session.get(_YOUTUBE_SEARCH_URL, params=params, timeout=20) as response:
                response.raise_for_status()
                payload = await response.json()
        except asyncio.TimeoutError:
            self._logger.warning("YouTube search timed out: channel=%s type=%s", channel_id, event_type)
            return []
        except aiohttp.ClientError:
            self._logger.exception("YouTube search failed: channel=%s type=%s", channel_id, event_type)
            return []

        items = payload.get("items")
        if not isinstance(items, list):
            self._logger.warning(
                "Unexpected YouTube response structure for channel %s: %s", channel_id, payload
            )
            return []
        return items

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
