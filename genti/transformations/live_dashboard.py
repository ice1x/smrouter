"""Transformation that builds Telegram-ready messages for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from telegram.helpers import escape_markdown

from genti.models import DashboardUpdate, LiveFeedState, Video
from genti.platform import TransformationStage


class LiveDashboardTransformation(TransformationStage[LiveFeedState, DashboardUpdate]):
    """Render a dashboard summary with live and upcoming broadcasts."""

    def __init__(self, *, show_upcoming: bool = True) -> None:
        self._show_upcoming = show_upcoming

    async def transform(self, data: LiveFeedState) -> DashboardUpdate:
        dashboard_text = self._build_dashboard_text(data)
        state = LiveFeedState(live=list(data.live), upcoming=list(data.upcoming))
        return DashboardUpdate(
            dashboard_text=dashboard_text,
            new_live_messages=[],
            state=state,
            generated_at=datetime.now(timezone.utc),
        )

    def _build_dashboard_text(self, state: LiveFeedState) -> str:
        now_local = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        lines: List[str] = []
        lines.append(f"🎥 {self._bold('Прямо сейчас в эфире')}")
        lines.extend(self._format_video_list(state.live, empty_placeholder="— (пусто)"))

        if self._show_upcoming:
            lines.append("")
            lines.append(f"⏳ {self._bold('Скоро начнутся')}")
            lines.extend(
                self._format_video_list(
                    state.upcoming,
                    empty_placeholder="— (ничего в ближайшее время)",
                )
            )

        lines.append("")
        lines.append(self._italic(f"обновлено: {now_local}"))
        return "\n".join(lines)

    def _format_video_list(self, videos: Iterable[Video], *, empty_placeholder: str) -> List[str]:
        if not videos:
            return [escape_markdown(empty_placeholder, version=2)]
        formatted: List[str] = []
        for video in videos:
            title = escape_markdown(video.title, version=2)
            channel = escape_markdown(video.channel_title, version=2)
            formatted.append(
                f"• [{title}]({video.url}) — {self._italic(channel, already_escaped=True)}"
            )
        return formatted

    def _bold(self, text: str) -> str:
        escaped = escape_markdown(text, version=2)
        return f"*{escaped}*"

    def _italic(self, text: str, *, already_escaped: bool = False) -> str:
        escaped = text if already_escaped else escape_markdown(text, version=2)
        return f"_{escaped}_"
