"""Lifecycle tests for the application startup/cleanup path in ``main``.

These reproduce the failure mode where a network timeout during
``application.initialize()`` was masked by the cleanup block calling
``updater.stop()`` on an updater that had never been started, raising
``RuntimeError: This Updater is not running!`` and hiding the real error.
"""

import asyncio

import pytest

from main import _run_application


class BoomError(Exception):
    """Stand-in for an initialize()-time network error (e.g. TimedOut)."""


class FakeUpdater:
    """Mirrors PTB's Updater: stop() asserts the updater is running."""

    def __init__(self):
        self.running = False
        self.start_polling_calls = 0
        self.stop_calls = 0

    async def start_polling(self, **_kwargs):
        self.start_polling_calls += 1
        self.running = True

    async def stop(self):
        if not self.running:
            raise RuntimeError("This Updater is not running!")
        self.stop_calls += 1
        self.running = False


class FakeApplication:
    """Mirrors PTB's Application: stop() asserts the application is running."""

    def __init__(self, *, updater=None, fail_initialize=None):
        self.updater = updater
        self._fail_initialize = fail_initialize
        self.running = False
        self.initialize_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.shutdown_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        if self._fail_initialize is not None:
            raise self._fail_initialize

    async def start(self):
        self.start_calls += 1
        self.running = True

    async def stop(self):
        if not self.running:
            raise RuntimeError("This Application is not running!")
        self.stop_calls += 1
        self.running = False

    async def shutdown(self):
        self.shutdown_calls += 1


async def _run(app, scheduler_task, stop_event):
    await _run_application(
        app,
        scheduler_task,
        stop_event,
        telegram_updates_poll_interval=1.0,
        telegram_updates_timeout=1.0,
    )


async def test_initialize_failure_propagates_original_error_not_cleanup_error():
    updater = FakeUpdater()
    boom = BoomError("connect timeout")
    app = FakeApplication(updater=updater, fail_initialize=boom)
    stop_event = asyncio.Event()

    with pytest.raises(BoomError) as excinfo:
        await _run(app, None, stop_event)

    # The original error must surface, not RuntimeError from cleanup.
    assert excinfo.value is boom
    # Nothing after the failed initialize() should have run.
    assert app.start_calls == 0
    assert updater.start_polling_calls == 0
    # Cleanup must not touch components that were never started...
    assert updater.stop_calls == 0
    assert app.stop_calls == 0
    # ...but shutdown() must still run to release resources.
    assert app.shutdown_calls == 1


async def test_full_lifecycle_stops_started_components_and_cancels_scheduler():
    updater = FakeUpdater()
    app = FakeApplication(updater=updater)
    stop_event = asyncio.Event()
    stop_event.set()  # exit the run loop immediately

    cancelled = asyncio.Event()

    async def scheduler_loop():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    scheduler_task = asyncio.create_task(scheduler_loop())
    await asyncio.sleep(0)  # let the task start

    await _run(app, scheduler_task, stop_event)

    assert app.initialize_calls == 1
    assert app.start_calls == 1
    assert updater.start_polling_calls == 1
    # Started components are torn down exactly once.
    assert updater.stop_calls == 1
    assert app.stop_calls == 1
    assert app.shutdown_calls == 1
    assert cancelled.is_set()


async def test_cleanup_when_start_fails_after_initialize():
    """initialize() succeeds, start() fails: updater/app were not started."""

    updater = FakeUpdater()
    app = FakeApplication(updater=updater)
    boom = BoomError("start failed")

    async def failing_start():
        app.start_calls += 1
        raise boom

    app.start = failing_start
    stop_event = asyncio.Event()

    with pytest.raises(BoomError) as excinfo:
        await _run(app, None, stop_event)

    assert excinfo.value is boom
    assert updater.start_polling_calls == 0
    assert updater.stop_calls == 0
    assert app.stop_calls == 0
    assert app.shutdown_calls == 1
