"""Lifecycle tests for the application startup/cleanup path in ``main``.

These reproduce the failure mode where a network timeout during
``application.initialize()`` was masked by the cleanup block calling
``updater.stop()`` on an updater that had never been started, raising
``RuntimeError: This Updater is not running!`` and hiding the real error.
"""

import asyncio

import pytest
from telegram.error import TimedOut

from main import _initialize_with_retry, _run_application


class BoomError(Exception):
    """Stand-in for a non-network initialize() error (e.g. invalid token)."""


class RecordingSleep:
    """Awaitable sleep that records its delays instead of waiting."""

    def __init__(self):
        self.calls = []

    async def __call__(self, delay):
        self.calls.append(delay)


class FlakyInitApp:
    """Application whose initialize() fails ``fail_times`` times, then succeeds."""

    def __init__(self, *, fail_times, error):
        self.fail_times = fail_times
        self.error = error
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        if self.initialize_calls <= self.fail_times:
            raise self.error


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

    def __init__(self, *, updater=None, fail_initialize=None, initialize_failures=0):
        self.updater = updater
        self._fail_initialize = fail_initialize
        # 0 => fail on every initialize() (permanent); N => fail the first N calls.
        self._initialize_failures = initialize_failures
        self.running = False
        self.initialize_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.shutdown_calls = 0

    async def initialize(self):
        self.initialize_calls += 1
        if self._fail_initialize is None:
            return
        if self._initialize_failures == 0 or self.initialize_calls <= self._initialize_failures:
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


# --- startup retry on transient network errors -----------------------------


async def test_initialize_retries_transient_network_error_then_succeeds():
    app = FlakyInitApp(fail_times=2, error=TimedOut("brief outage"))
    sleep = RecordingSleep()

    await _initialize_with_retry(app, attempts=5, backoff=1.5, sleep=sleep)

    assert app.initialize_calls == 3  # two failures + one success
    assert sleep.calls == [1.5, 1.5]  # backoff between attempts only


async def test_initialize_gives_up_after_max_attempts_and_raises_last_error():
    err = TimedOut("still down")
    app = FlakyInitApp(fail_times=99, error=err)
    sleep = RecordingSleep()

    with pytest.raises(TimedOut) as excinfo:
        await _initialize_with_retry(app, attempts=3, backoff=0.2, sleep=sleep)

    assert excinfo.value is err
    assert app.initialize_calls == 3  # exactly `attempts` tries
    assert sleep.calls == [0.2, 0.2]  # slept between tries, not after the last


async def test_initialize_does_not_retry_non_network_error():
    app = FlakyInitApp(fail_times=1, error=BoomError("invalid token"))
    sleep = RecordingSleep()

    with pytest.raises(BoomError):
        await _initialize_with_retry(app, attempts=5, backoff=1.0, sleep=sleep)

    assert app.initialize_calls == 1  # failed fast, no retry
    assert sleep.calls == []


async def test_run_application_recovers_from_transient_initialize_failure():
    updater = FakeUpdater()
    app = FakeApplication(
        updater=updater,
        fail_initialize=TimedOut("brief outage"),
        initialize_failures=1,
    )
    stop_event = asyncio.Event()
    stop_event.set()  # exit the run loop immediately after startup
    sleep = RecordingSleep()

    await _run_application(
        app,
        None,
        stop_event,
        telegram_updates_poll_interval=1.0,
        telegram_updates_timeout=1.0,
        startup_attempts=3,
        startup_backoff=0.2,
        sleep=sleep,
    )

    assert app.initialize_calls == 2  # one retry, then success
    assert sleep.calls == [0.2]
    assert app.start_calls == 1
    assert updater.start_polling_calls == 1
    # Full clean teardown still happens.
    assert updater.stop_calls == 1
    assert app.stop_calls == 1
    assert app.shutdown_calls == 1
