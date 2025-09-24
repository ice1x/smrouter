"""Entry point for the modular Telegram ↔ YouTube live dashboard platform."""

import asyncio
import logging
import os
from typing import Dict

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from genti.connectors.telegram import TelegramDashboardConnector
from genti.connectors.youtube import YouTubeLiveConnector
from genti.platform import Pipeline, PipelineConfig
from genti.transformations.live_dashboard import LiveDashboardTransformation

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
YT_API_KEY = os.getenv("YT_API_KEY")
WHITELIST = [c.strip() for c in os.getenv("WHITELIST", "").split(",") if c.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "90"))
SHOW_UPCOMING = os.getenv("SHOW_UPCOMING", "1") == "1"
MAX_CONSECUTIVE_FAILURES = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "4"))


async def start_cmd(update, context):
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
        "Запускаем платформу: whitelist=%s poll_seconds=%s show_upcoming=%s",
        ",".join(WHITELIST),
        POLL_SECONDS,
        SHOW_UPCOMING,
    )

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))

    youtube_connector = YouTubeLiveConnector(
        api_key=YT_API_KEY,
        channel_ids=WHITELIST,
        show_upcoming=SHOW_UPCOMING,
        logger=logging.getLogger("genti.youtube"),
    )
    transformation = LiveDashboardTransformation(show_upcoming=SHOW_UPCOMING)
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

    async def pipeline_job(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await pipeline.run_once()
            failure_state["count"] = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_state["count"] += 1
            logger.exception(
                "Pipeline run failed (%d/%d)",
                failure_state["count"],
                MAX_CONSECUTIVE_FAILURES,
            )
            if failure_state["count"] >= MAX_CONSECUTIVE_FAILURES:
                logger.critical("Maximum failure threshold reached, shutting down application")
                await context.application.stop()

    if application.job_queue is None:
        raise RuntimeError("Application was created without a JobQueue; cannot schedule pipeline")

    application.job_queue.run_repeating(pipeline_job, interval=POLL_SECONDS, first=0)
    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
