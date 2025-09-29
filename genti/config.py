"""Default configuration values for the genti platform."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Set

# Environment variable names
LOG_LEVEL_ENV = "LOG_LEVEL"
POLL_SECONDS_ENV = "POLL_SECONDS"
SHOW_UPCOMING_ENV = "SHOW_UPCOMING"
MAX_CONSECUTIVE_FAILURES_ENV = "MAX_CONSECUTIVE_FAILURES"
TELEGRAM_UPDATES_POLL_INTERVAL_ENV = "TELEGRAM_UPDATES_POLL_INTERVAL"
TELEGRAM_UPDATES_TIMEOUT_ENV = "TELEGRAM_UPDATES_TIMEOUT"
CACHE_PATH_ENV = "CACHE_PATH"

# Default values
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_POLL_SECONDS = 900
DEFAULT_SHOW_UPCOMING = False
DEFAULT_MAX_CONSECUTIVE_FAILURES = 4
DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL = float(DEFAULT_POLL_SECONDS)
DEFAULT_TELEGRAM_UPDATES_TIMEOUT = min(float(DEFAULT_POLL_SECONDS), 50.0)
DEFAULT_CACHE_PATH = "cache.json"

logger = logging.getLogger(__name__)


def _read_positive_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning("Invalid value for %s=%r, falling back to %s", name, raw_value, default)
        return default
    if value < 0:
        logger.warning("Negative value for %s=%s is not allowed; using %s", name, value, default)
        return default
    return value


def _parse_csv_env(name: str) -> List[str]:
    return [chunk.strip() for chunk in os.getenv(name, "").split(",") if chunk.strip()]


LOG_LEVEL = os.getenv(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL)
POLL_SECONDS = int(os.getenv(POLL_SECONDS_ENV, str(DEFAULT_POLL_SECONDS)))
SHOW_UPCOMING = os.getenv(SHOW_UPCOMING_ENV, "1" if DEFAULT_SHOW_UPCOMING else "0") == "1"
MAX_CONSECUTIVE_FAILURES = int(
    os.getenv(MAX_CONSECUTIVE_FAILURES_ENV, str(DEFAULT_MAX_CONSECUTIVE_FAILURES))
)
TELEGRAM_UPDATES_POLL_INTERVAL = _read_positive_float_env(
    TELEGRAM_UPDATES_POLL_INTERVAL_ENV,
    DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL,
)
TELEGRAM_UPDATES_TIMEOUT = _read_positive_float_env(
    TELEGRAM_UPDATES_TIMEOUT_ENV,
    min(float(POLL_SECONDS), DEFAULT_TELEGRAM_UPDATES_TIMEOUT),
)
if TELEGRAM_UPDATES_TIMEOUT > 50.0:
    logger.warning(
        "TELEGRAM_UPDATES_TIMEOUT=%s exceeds Telegram API limit (50s); clamping to 50s",
        TELEGRAM_UPDATES_TIMEOUT,
    )
    TELEGRAM_UPDATES_TIMEOUT = 50.0

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
YT_API_KEY = os.getenv("YT_API_KEY")
WHITELIST = _parse_csv_env("WHITELIST")
TELEGRAM_ALLOWED_USER_IDS = _parse_csv_env("TELEGRAM_ALLOWED_USER_IDS")
_cache_path_raw = os.getenv(CACHE_PATH_ENV, DEFAULT_CACHE_PATH).strip()
if not _cache_path_raw:
    _cache_path_raw = DEFAULT_CACHE_PATH
CACHE_PATH = Path(_cache_path_raw).expanduser()

ALLOWED_ACTOR_IDS: Set[str] = set(TELEGRAM_ALLOWED_USER_IDS)
if TELEGRAM_CHANNEL_ID:
    normalized_channel_id = TELEGRAM_CHANNEL_ID.strip()
    if normalized_channel_id:
        ALLOWED_ACTOR_IDS.add(normalized_channel_id)
        if normalized_channel_id.lstrip("-").isdigit():
            ALLOWED_ACTOR_IDS.add(str(int(normalized_channel_id)))


__all__ = [
    "LOG_LEVEL_ENV",
    "POLL_SECONDS_ENV",
    "SHOW_UPCOMING_ENV",
    "MAX_CONSECUTIVE_FAILURES_ENV",
    "TELEGRAM_UPDATES_POLL_INTERVAL_ENV",
    "TELEGRAM_UPDATES_TIMEOUT_ENV",
    "CACHE_PATH_ENV",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_SHOW_UPCOMING",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL",
    "DEFAULT_TELEGRAM_UPDATES_TIMEOUT",
    "DEFAULT_CACHE_PATH",
    "LOG_LEVEL",
    "POLL_SECONDS",
    "SHOW_UPCOMING",
    "MAX_CONSECUTIVE_FAILURES",
    "TELEGRAM_UPDATES_POLL_INTERVAL",
    "TELEGRAM_UPDATES_TIMEOUT",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHANNEL_ID",
    "YT_API_KEY",
    "WHITELIST",
    "TELEGRAM_ALLOWED_USER_IDS",
    "ALLOWED_ACTOR_IDS",
    "CACHE_PATH",
]
