"""Template loader utilities for Telegram messages."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping

DEFAULT_TEMPLATE_FILE = Path(__file__).with_name("telegram_default_ru.json")
TEMPLATE_PATH_ENV = "TELEGRAM_TEMPLATE_PATH"


@dataclass(frozen=True)
class TelegramTemplates:
    """Container object for Telegram-facing message templates."""

    start_response: str
    unauthorized_start: str
    initialization_message: str
    dashboard_header: str
    empty_with_errors: str
    empty_without_errors: str


def _normalize_mapping(raw_mapping: Mapping[str, str] | MutableMapping[str, str]) -> Mapping[str, str]:
    return {str(key): str(value) for key, value in raw_mapping.items()}


def _load_mapping(path: Path | None) -> Mapping[str, str]:
    target_path = path if path is not None else DEFAULT_TEMPLATE_FILE
    with target_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, Mapping):
        raise ValueError(f"Template file {target_path} does not contain a mapping")
    normalized = _normalize_mapping(data)
    required_keys = {
        "start_response",
        "unauthorized_start",
        "initialization_message",
        "dashboard_header",
        "empty_with_errors",
        "empty_without_errors",
    }
    missing = sorted(required_keys.difference(normalized))
    if missing:
        raise ValueError(f"Template file {target_path} is missing keys: {', '.join(missing)}")
    return normalized


def load_templates(path: str | os.PathLike[str] | None = None) -> TelegramTemplates:
    """Load Telegram templates from *path* or use the bundled defaults."""

    candidate_path = Path(path) if path is not None else None
    mapping = _load_mapping(candidate_path)
    return TelegramTemplates(
        start_response=mapping["start_response"],
        unauthorized_start=mapping["unauthorized_start"],
        initialization_message=mapping["initialization_message"],
        dashboard_header=mapping["dashboard_header"],
        empty_with_errors=mapping["empty_with_errors"],
        empty_without_errors=mapping["empty_without_errors"],
    )


TELEGRAM_TEMPLATES = load_templates(os.getenv(TEMPLATE_PATH_ENV))

__all__ = [
    "TelegramTemplates",
    "TELEGRAM_TEMPLATES",
    "load_templates",
    "TEMPLATE_PATH_ENV",
]
