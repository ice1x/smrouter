import asyncio

import pytest

pytest.importorskip("telegram")

from src.models import LiveFeedState, Video
from src.templates import TELEGRAM_TEMPLATES
from src.transformations.live_dashboard import LiveDashboardTransformation


def test_transformation_builds_dashboard_and_skips_extra_messages():
    transformation = LiveDashboardTransformation()
    state = LiveFeedState(
        live=[
            Video(
                video_id="vid1",
                title="Title_1 [test]",
                channel_title="Ch*1*",
                url="https://youtu.be/vid1",
                viewer_count=321,
            )
        ],
        upcoming=[
            Video(
                video_id="vid2",
                title="Another",
                channel_title="Channel",
                url="https://youtu.be/vid2",
                viewer_count=12,
            )
        ],
    )

    async def run_updates():
        first = await transformation.transform(state)
        second = await transformation.transform(state)
        return first, second

    first_update, second_update = asyncio.run(run_updates())

    assert TELEGRAM_TEMPLATES.dashboard_header in first_update.dashboard_text
    assert "CH\\*1\\* \\[321\\]" in first_update.dashboard_text
    assert "\n https://youtu.be/vid1" in first_update.dashboard_text
    assert first_update.new_live_messages == []
    assert second_update.new_live_messages == []


def test_transformation_handles_empty_lists():
    transformation = LiveDashboardTransformation()
    state = LiveFeedState(live=[], upcoming=[])

    update = asyncio.run(transformation.transform(state))
    assert TELEGRAM_TEMPLATES.dashboard_header in update.dashboard_text
    assert TELEGRAM_TEMPLATES.empty_without_errors in update.dashboard_text
    assert update.new_live_messages == []


def test_transformation_reports_errors():
    transformation = LiveDashboardTransformation()
    state = LiveFeedState(
        live=[],
        upcoming=[],
        errors=["YouTube API quota exceeded—updates are temporarily unavailable."],
    )

    update = asyncio.run(transformation.transform(state))
    assert TELEGRAM_TEMPLATES.empty_with_errors in update.dashboard_text
    assert "⚠️" not in update.dashboard_text
    assert "ℹ️" not in update.dashboard_text
