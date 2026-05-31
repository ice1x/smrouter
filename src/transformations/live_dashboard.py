"""Transformation that builds Telegram-ready messages for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from telegram.helpers import escape_markdown

from src.models import DashboardUpdate, LiveFeedState, Video
from src.platform import TransformationStage
from src.templates import TELEGRAM_TEMPLATES


class LiveDashboardTransformation(TransformationStage[LiveFeedState, DashboardUpdate]):
    """Render a dashboard summary with the currently active broadcasts."""

    async def transform(self, data: LiveFeedState) -> DashboardUpdate:
        dashboard_text = self._build_dashboard_text(data)
        state = LiveFeedState(
            live=list(data.live),
            upcoming=list(data.upcoming),
            errors=list(data.errors),
        )
        return DashboardUpdate(
            dashboard_text=dashboard_text,
            new_live_messages=[],
            state=state,
            generated_at=datetime.now(timezone.utc),
        )

    def _build_dashboard_text(self, state: LiveFeedState) -> str:
        lines: List[str] = [TELEGRAM_TEMPLATES.dashboard_header, ""]

        empty_placeholder = (
            TELEGRAM_TEMPLATES.empty_with_errors
            if state.errors
            else TELEGRAM_TEMPLATES.empty_without_errors
        )
        lines.extend(self._format_video_list(state.live, empty_placeholder=empty_placeholder))

        if state.errors:
            lines.append("")
            lines.extend(self._format_errors(state.errors))

        return "\n".join(lines)

    def _format_video_list(self, videos: Iterable[Video], *, empty_placeholder: str) -> List[str]:
        if not videos:
            return [escape_markdown(empty_placeholder, version=2)]
        formatted: List[str] = []
        for video in videos:
            channel = escape_markdown(video.channel_title.upper(), version=2)
            viewers = self._format_viewer_count(video.viewer_count)
            short_url = escape_markdown(self._short_url(video), version=2)
            formatted.append(f"{channel} \\[{viewers}\\]")
            formatted.append(f" {short_url}")
        return formatted

    def _format_errors(self, errors: Iterable[str]) -> List[str]:
        return [escape_markdown(error, version=2) for error in errors]

    def _format_viewer_count(self, viewer_count: int | None) -> str:
        if viewer_count is None:
            return "?"
        return f"{viewer_count:,}".replace(",", " ")

    def _short_url(self, video: Video) -> str:
        if video.video_id:
            return f"https://youtu.be/{video.video_id}"
        return video.url
