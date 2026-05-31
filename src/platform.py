"""Core abstractions and orchestration helpers for the live dashboard platform."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Generic, Optional, Protocol, TypeVar


from src.exceptions import FatalPipelineError


TSource = TypeVar("TSource")
TResult = TypeVar("TResult")


class SourceConnector(Protocol[TSource]):
    """Collects raw data from an external system."""

    async def fetch(self) -> TSource:
        """Retrieve fresh data from the source."""


class TransformationStage(Protocol[TSource, TResult]):
    """Transforms raw connector data into a payload consumable by a sink."""

    async def transform(self, data: TSource) -> TResult:
        """Perform the transformation."""


class SinkConnector(Protocol[TResult]):
    """Emits processed data to an external destination."""

    async def push(self, data: TResult) -> None:
        """Deliver the processed payload."""


@dataclass
class PipelineConfig:
    """Configuration options shared by all pipeline runs."""

    poll_interval: float
    max_consecutive_failures: int = 3


class Pipeline(Generic[TSource, TResult]):
    """Coordinates a source connector, transformation stage and sink connector."""

    def __init__(
        self,
        source: SourceConnector[TSource],
        transformation: TransformationStage[TSource, TResult],
        sink: SinkConnector[TResult],
        config: PipelineConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._source = source
        self._transformation = transformation
        self._sink = sink
        self._config = config
        self._logger = logger or logging.getLogger(__name__)

    async def run_once(self) -> None:
        """Execute a single ETL cycle."""

        raw_data = await self._source.fetch()
        self._logger.debug("Pipeline fetched raw data: %s", raw_data)
        processed = await self._transformation.transform(raw_data)
        self._logger.debug("Pipeline transformed data: %s", processed)
        await self._sink.push(processed)

    async def run_forever(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Continuously execute the pipeline until ``stop_event`` is set."""

        consecutive_failures = 0
        stop_event = stop_event or asyncio.Event()

        while not stop_event.is_set():
            try:
                await self.run_once()
                consecutive_failures = 0
                self._logger.info("Pipeline iteration finished successfully")
            except asyncio.CancelledError:
                raise
            except FatalPipelineError:
                consecutive_failures = 0
                self._logger.critical("Fatal pipeline error encountered; aborting")
                raise
            except Exception:  # pragma: no cover - defensive catch for runtime stability
                consecutive_failures += 1
                self._logger.exception(
                    "Pipeline iteration failed (%d/%d)",
                    consecutive_failures,
                    self._config.max_consecutive_failures,
                )
                if consecutive_failures >= self._config.max_consecutive_failures:
                    self._logger.critical(
                        "Maximum number of consecutive failures reached (%d)",
                        self._config.max_consecutive_failures,
                    )
                    raise

            # honour stop_event without busy waiting
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._config.poll_interval)
            except asyncio.TimeoutError:
                continue
