"""Transformation that builds Telegram-ready messages for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from telegram.helpers import escape_markdown

from genti.models import DashboardUpdate, LiveFeedState, Video
from genti.platform import TransformationStage


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
        now_local = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        lines: List[str] = []
        lines.append(f"🎥 {self._bold('Прямо сейчас в эфире')}")

        empty_placeholder = "— данные недоступны" if state.errors else "— (пусто)"
        lines.extend(self._format_video_list(state.live, empty_placeholder=empty_placeholder))

        if not state.errors:
            lines.append("")
        lines.append(self._italic(f"обновлено: {now_local}"))
        return "\n".join(lines)

    def _format_video_list(self, videos: Iterable[Video], *, empty_placeholder: str) -> List[str]:
        if not videos:
            return [escape_markdown(empty_placeholder, version=2)]
        formatted: List[str] = []
        for video in videos:
            title = escape_markdown(video.title, version=2)
            url = escape_markdown(video.url, version=2)
            formatted.append(f"• {title} \\- {url}")
        return formatted

    def _bold(self, text: str) -> str:
        escaped = escape_markdown(text, version=2)
        return f"*{escaped}*"

    def _italic(self, text: str, *, already_escaped: bool = False) -> str:
        escaped = text if already_escaped else escape_markdown(text, version=2)
        return f"_{escaped}_"
