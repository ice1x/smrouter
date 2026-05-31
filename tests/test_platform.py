import asyncio

import pytest

from src.exceptions import FatalPipelineError
from src.platform import Pipeline, PipelineConfig


class DummySource:
    def __init__(self):
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        return "raw"


class DummyTransformation:
    def __init__(self):
        self.calls = 0

    async def transform(self, data):
        self.calls += 1
        assert data == "raw"
        return data.upper()


class DummySink:
    def __init__(self):
        self.payloads = []

    async def push(self, data):
        self.payloads.append(data)


class FatalSink:
    async def push(self, data):
        raise FatalPipelineError("fatal")


def test_pipeline_run_once_executes_all_stages():
    source = DummySource()
    transformation = DummyTransformation()
    sink = DummySink()
    pipeline = Pipeline(source, transformation, sink, PipelineConfig(poll_interval=0.01))

    asyncio.run(pipeline.run_once())

    assert source.calls == 1
    assert transformation.calls == 1
    assert sink.payloads == ["RAW"]


class FailingSource:
    async def fetch(self):
        raise RuntimeError("boom")


def test_pipeline_run_forever_raises_after_max_failures():
    pipeline = Pipeline(
        FailingSource(),
        DummyTransformation(),
        DummySink(),
        PipelineConfig(poll_interval=0.01, max_consecutive_failures=2),
    )

    async def runner():
        stop_event = asyncio.Event()
        await pipeline.run_forever(stop_event=stop_event)

    with pytest.raises(RuntimeError):
        asyncio.run(runner())


def test_pipeline_run_forever_bubbles_fatal_error():
    pipeline = Pipeline(
        DummySource(),
        DummyTransformation(),
        FatalSink(),
        PipelineConfig(poll_interval=0.01, max_consecutive_failures=5),
    )

    async def runner():
        stop_event = asyncio.Event()
        await pipeline.run_forever(stop_event=stop_event)

    with pytest.raises(FatalPipelineError):
        asyncio.run(runner())
