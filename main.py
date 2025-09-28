"""Entry point for the modular Telegram ↔ YouTube live dashboard platform."""

import asyncio
import logging
import signal
from contextlib import suppress
from typing import Awaitable, Callable, Dict

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from genti.config import (
    ALLOWED_ACTOR_IDS,
    LOG_LEVEL,
    MAX_CONSECUTIVE_FAILURES,
    POLL_SECONDS,
    SHOW_UPCOMING,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID,
    TELEGRAM_UPDATES_POLL_INTERVAL,
    TELEGRAM_UPDATES_TIMEOUT,
    WHITELIST,
    YT_API_KEY,
)
from genti.connectors.telegram import TelegramDashboardConnector
from genti.connectors.youtube import YouTubeLiveConnector
from genti.exceptions import FatalPipelineError
from genti.platform import Pipeline, PipelineConfig
from genti.transformations.live_dashboard import LiveDashboardTransformation

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

def _is_actor_allowed(update: Update) -> bool:
    if not ALLOWED_ACTOR_IDS:
        return False

    user = getattr(update, "effective_user", None)
    if user is not None:
        user_id = getattr(user, "id", None)
        if user_id is not None and str(user_id) in ALLOWED_ACTOR_IDS:
            return True

    chat = getattr(update, "effective_chat", None)
    if chat is not None:
        chat_id = getattr(chat, "id", None)
        if chat_id is not None and str(chat_id) in ALLOWED_ACTOR_IDS:
            return True

    return False


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_actor_allowed(update):
        user = getattr(getattr(update, "effective_user", None), "id", None)
        chat = getattr(getattr(update, "effective_chat", None), "id", None)
        logger.warning(
            "Unauthorized start command: user_id=%s chat_id=%s",  # noqa: G004
            user,
            chat,
        )
        if update.message is not None:
            await update.message.reply_text("Доступ к этому боту ограничен владельцем.")
        return

    await update.message.reply_text(
        "Ок! Дашборд будет поддерживаться автоматически. Закрепите этот пост, если он не закрепился сам."
    )


def _validate_environment() -> None:
    missing: Dict[str, bool] = {
        "TELEGRAM_BOT_TOKEN": bool(TELEGRAM_BOT_TOKEN),
        "TELEGRAM_CHANNEL_ID": bool(TELEGRAM_CHANNEL_ID),
        "YT_API_KEY": bool(YT_API_KEY),
        "WHITELIST": bool(WHITELIST),
    }
    if not all(missing.values()):
        logger.error("Missing required environment configuration: %s", missing)
        raise SystemExit(
            "Не заданы переменные окружения: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, YT_API_KEY, WHITELIST"
        )


async def main() -> None:
    _validate_environment()

    logger.info(
        "Запускаем платформу: whitelist=%s poll_seconds=%s show_upcoming=%s tg_poll_interval=%s tg_timeout=%s",
        ",".join(WHITELIST),
        POLL_SECONDS,
        SHOW_UPCOMING,
        TELEGRAM_UPDATES_POLL_INTERVAL,
        TELEGRAM_UPDATES_TIMEOUT,
    )

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))

    youtube_connector = YouTubeLiveConnector(
        api_key=YT_API_KEY,
        channel_ids=WHITELIST,
        show_upcoming=SHOW_UPCOMING,
        logger=logging.getLogger("genti.youtube"),
    )
    transformation = LiveDashboardTransformation()
    telegram_connector = TelegramDashboardConnector(
        application=application,
        channel_id=TELEGRAM_CHANNEL_ID,
        logger=logging.getLogger("genti.telegram"),
    )
    pipeline = Pipeline(
        source=youtube_connector,
        transformation=transformation,
        sink=telegram_connector,
        config=PipelineConfig(
            poll_interval=POLL_SECONDS,
            max_consecutive_failures=MAX_CONSECUTIVE_FAILURES,
        ),
        logger=logging.getLogger("genti.pipeline"),
    )

    failure_state = {"count": 0}
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    async def run_pipeline_iteration(stop_application: Callable[[], Awaitable[None]]) -> None:
        try:
            await pipeline.run_once()
            failure_state["count"] = 0
        except asyncio.CancelledError:
            raise
        except FatalPipelineError:
            logger.exception("Fatal pipeline error encountered; requesting shutdown")
            await stop_application()
        except Exception:
            failure_state["count"] += 1
            logger.exception(
                "Pipeline run failed (%d/%d)",
                failure_state["count"],
                MAX_CONSECUTIVE_FAILURES,
            )
            if failure_state["count"] >= MAX_CONSECUTIVE_FAILURES:
                logger.critical("Maximum failure threshold reached, shutting down application")
                await stop_application()

    async def request_application_stop() -> None:
        if not stop_event.is_set():
            stop_event.set()

    scheduler_task = None

    if application.job_queue is not None:
        async def pipeline_job(context: ContextTypes.DEFAULT_TYPE) -> None:
            await run_pipeline_iteration(request_application_stop)

        application.job_queue.run_repeating(pipeline_job, interval=POLL_SECONDS, first=0)
    else:
        logger.warning("Application has no JobQueue; falling back to asyncio-based scheduler")

        async def scheduler_loop() -> None:
            try:
                while not stop_event.is_set():
                    await run_pipeline_iteration(request_application_stop)
                    if stop_event.is_set():
                        break
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
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
                poll_interval=TELEGRAM_UPDATES_POLL_INTERVAL,
                timeout=TELEGRAM_UPDATES_TIMEOUT,
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
