"""Logging utilities for the genti platform."""

from __future__ import annotations

import logging
from typing import Iterable


class RedactingFormatter(logging.Formatter):
    """Formatter that masks sensitive values in log output."""

    def __init__(self, *args, redactions: Iterable[str] = (), placeholder: str = "***REDACTED***", **kwargs):
        super().__init__(*args, **kwargs)
        self._placeholder = placeholder
        self._redactions = [str(secret) for secret in redactions if secret]

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - match logging API
        rendered = super().format(record)
        for secret in self._redactions:
            rendered = rendered.replace(secret, self._placeholder)
        return rendered


def configure_logging(log_level: str, *, redactions: Iterable[str] = (), stream=None) -> None:
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", redactions=redactions
    )
    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )
