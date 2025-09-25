import asyncio

import pytest

pytest.importorskip("telegram")

from genti.models import LiveFeedState, Video
from genti.transformations.live_dashboard import LiveDashboardTransformation


def test_transformation_builds_dashboard_and_skips_extra_messages():
    transformation = LiveDashboardTransformation()
    state = LiveFeedState(
        live=[
            Video(
                video_id="vid1",
                title="Title_1 [test]",
                channel_title="Ch*1*",
                url="https://youtu.be/vid1",
            )
        ],
        upcoming=[
            Video(
                video_id="vid2",
                title="Another",
                channel_title="Channel",
                url="https://youtu.be/vid2",
            )
        ],
    )

    async def run_updates():
        first = await transformation.transform(state)
        second = await transformation.transform(state)
        return first, second

    first_update, second_update = asyncio.run(run_updates())

    assert "Прямо сейчас" in first_update.dashboard_text
    assert "Title\\_1" in first_update.dashboard_text
    assert "Скоро начнутся" not in first_update.dashboard_text
    assert first_update.new_live_messages == []
    assert second_update.new_live_messages == []


def test_transformation_handles_empty_lists():
    transformation = LiveDashboardTransformation()
    state = LiveFeedState(live=[], upcoming=[])

    update = asyncio.run(transformation.transform(state))
    assert "(пусто)" in update.dashboard_text
    assert update.new_live_messages == []
