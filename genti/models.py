"""Common dataclasses shared across the live dashboard platform."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List


@dataclass(frozen=True)
class Video:
    """Normalized representation of a YouTube search result."""

    video_id: str
    title: str
    channel_title: str
    url: str


@dataclass
class LiveFeedState:
    """Snapshot of live and upcoming broadcasts for the whitelist."""

    live: List[Video]
    upcoming: List[Video]


@dataclass
class DashboardUpdate:
    """Result of transforming a ``LiveFeedState`` for Telegram delivery."""

    dashboard_text: str
    new_live_messages: List[str]
    state: LiveFeedState
    generated_at: datetime
