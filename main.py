"""Entry point for the modular Telegram ↔ YouTube live dashboard platform."""

import asyncio
import logging
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Iterable, List

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from src.config import ApplicationConfig, ConfigurationError, PipelineMapping, load_config
from src.connectors.telegram import TelegramDashboardConnector
from src.connectors.youtube import YouTubeLiveConnector
from src.exceptions import FatalPipelineError
from src.logging_utils import configure_logging
from src.platform import Pipeline, PipelineConfig
from src.transformations.live_dashboard import LiveDashboardTransformation
from src.templates import TELEGRAM_TEMPLATES

logger = logging.getLogger(__name__)


@dataclass
class ManagedPipeline:
    """Pipeline instance bound to a Telegram destination."""

    mapping: PipelineMapping
    pipeline: Pipeline


def _is_actor_allowed(update: Update, allowed_actor_ids: Iterable[str]) -> bool:
    allowed_ids = set(str(actor) for actor in allowed_actor_ids)
    if not allowed_ids:
        return False

    user = getattr(update, "effective_user", None)
    if user is not None:
        user_id = getattr(user, "id", None)
        if user_id is not None and str(user_id) in allowed_ids:
            return True

    chat = getattr(update, "effective_chat", None)
    if chat is not None:
        chat_id = getattr(chat, "id", None)
        if chat_id is not None and str(chat_id) in allowed_ids:
            return True

    return False


def _build_start_handler(allowed_actor_ids: Iterable[str]):
    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_actor_allowed(update, allowed_actor_ids):
            user = getattr(getattr(update, "effective_user", None), "id", None)
            chat = getattr(getattr(update, "effective_chat", None), "id", None)
            logger.warning(
                "Unauthorized start command: user_id=%s chat_id=%s",  # noqa: G004
                user,
                chat,
            )
            if update.message is not None:
                await update.message.reply_text(TELEGRAM_TEMPLATES.unauthorized_start)
            return

        if update.message is not None:
            await update.message.reply_text(TELEGRAM_TEMPLATES.start_response)

    return start_cmd


def _validate_config(config: ApplicationConfig) -> None:
    if not config.pipelines:
        raise SystemExit("Configuration must include at least one pipeline mapping.")

    for mapping in config.pipelines:
        if not mapping.stream_ids:
            raise SystemExit(
                f"Pipeline for Telegram channel {mapping.telegram_channel_id} has no configured streams."
            )


def _format_pipeline_summary(pipelines: Iterable[PipelineMapping]) -> str:
    summary: List[str] = []
    for pipeline in pipelines:
        streams = ", ".join(f"{stream.name} ({stream.youtube_id})" for stream in pipeline.streams)
        summary.append(f"tg={pipeline.telegram_channel_id}: {streams}")
    return "; ".join(summary)


def _build_managed_pipelines(config: ApplicationConfig, application) -> List[ManagedPipeline]:
    managed: List[ManagedPipeline] = []
    for mapping in config.pipelines:
        youtube_connector = YouTubeLiveConnector(
            api_key=config.auth.youtube_api_key,
            channel_ids=mapping.stream_ids,
            show_upcoming=config.show_upcoming,
            logger=logging.getLogger(f"genti.youtube.{mapping.telegram_channel_id}"),
            uploads_cache_path=config.cache_path,
        )
        transformation = LiveDashboardTransformation()
        telegram_connector = TelegramDashboardConnector(
            application=application,
            channel_id=mapping.telegram_channel_id,
            logger=logging.getLogger(f"genti.telegram.{mapping.telegram_channel_id}"),
        )
        pipeline = Pipeline(
            source=youtube_connector,
            transformation=transformation,
            sink=telegram_connector,
            config=PipelineConfig(
                poll_interval=config.poll_seconds,
                max_consecutive_failures=config.max_consecutive_failures,
            ),
            logger=logging.getLogger(f"genti.pipeline.{mapping.telegram_channel_id}"),
        )
        managed.append(ManagedPipeline(mapping=mapping, pipeline=pipeline))
    return managed


def _build_failure_state(managed_pipelines: Iterable[ManagedPipeline]) -> Dict[str, int]:
    return {pipeline.mapping.telegram_channel_id: 0 for pipeline in managed_pipelines}


async def main(config_path: str | None = None) -> None:
    try:
        config = load_config(config_path)
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    configure_logging(
        config.log_level,
        redactions=(config.auth.telegram_bot_token, config.auth.youtube_api_key),
    )
    _validate_config(config)

    logger.info(
        "Starting platform: pipelines=%s poll_seconds=%s show_upcoming=%s tg_poll_interval=%s tg_timeout=%s",
        _format_pipeline_summary(config.pipelines),
        config.poll_seconds,
        config.show_upcoming,
        config.telegram_updates_poll_interval,
        config.telegram_updates_timeout,
    )

    application = ApplicationBuilder().token(config.auth.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", _build_start_handler(config.allowed_actor_ids)))

    managed_pipelines = _build_managed_pipelines(config, application)
    failure_state = _build_failure_state(managed_pipelines)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    async def run_pipeline_iteration(
        pipeline: ManagedPipeline, stop_application: Callable[[], Awaitable[None]]
    ) -> None:
        try:
            await pipeline.pipeline.run_once()
            failure_state[pipeline.mapping.telegram_channel_id] = 0
        except asyncio.CancelledError:
            raise
        except FatalPipelineError:
            logger.exception(
                "Fatal pipeline error encountered for %s; requesting shutdown", pipeline.mapping.telegram_channel_id
            )
            await stop_application()
        except Exception:
            failure_state[pipeline.mapping.telegram_channel_id] += 1
            logger.exception(
                "Pipeline run failed for %s (%d/%d)",
                pipeline.mapping.telegram_channel_id,
                failure_state[pipeline.mapping.telegram_channel_id],
                config.max_consecutive_failures,
            )
            if failure_state[pipeline.mapping.telegram_channel_id] >= config.max_consecutive_failures:
                logger.critical("Maximum failure threshold reached; shutting down application")
                await stop_application()

    async def request_application_stop() -> None:
        if not stop_event.is_set():
            stop_event.set()

    scheduler_task = None

    if application.job_queue is not None:
        async def pipeline_job(context: ContextTypes.DEFAULT_TYPE) -> None:
            for pipeline in managed_pipelines:
                await run_pipeline_iteration(pipeline, request_application_stop)

        application.job_queue.run_repeating(pipeline_job, interval=config.poll_seconds, first=0)
    else:
        logger.warning("Application has no JobQueue; falling back to asyncio-based scheduler")

        async def scheduler_loop() -> None:
            try:
                while not stop_event.is_set():
                    for pipeline in managed_pipelines:
                        await run_pipeline_iteration(pipeline, request_application_stop)
                        if stop_event.is_set():
                            break
                    if stop_event.is_set():
                        break
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=config.poll_seconds)
                    except asyncio.TimeoutError:
                        continue
            except asyncio.CancelledError:
                raise

        scheduler_task = asyncio.create_task(scheduler_loop())

    try:
        await application.initialize()
        await application.start()

        if application.updater is not None:
            await application.updater.start_polling(
                poll_interval=config.telegram_updates_poll_interval,
                timeout=config.telegram_updates_timeout,
            )

        await stop_event.wait()
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task

        if application.updater is not None:
            await application.updater.stop()

        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
