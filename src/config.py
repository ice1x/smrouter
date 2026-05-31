"""Configuration loading helpers for the smrouter platform."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Set, Tuple

try:  # pragma: no cover - exercised indirectly via fallback logic
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency missing in some environments
    yaml = None

# Environment variable names
CONFIG_PATH_ENV = "CONFIG_PATH"

# Default values
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_POLL_SECONDS = 900
DEFAULT_SHOW_UPCOMING = False
DEFAULT_MAX_CONSECUTIVE_FAILURES = 4
DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL = float(DEFAULT_POLL_SECONDS)
DEFAULT_TELEGRAM_UPDATES_TIMEOUT = min(float(DEFAULT_POLL_SECONDS), 50.0)
DEFAULT_CACHE_PATH = "cache.json"
DEFAULT_CONFIG_PATH = "config.yaml"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamConfig:
    """Mapping between a YouTube stream/channel identifier and a friendly name."""

    youtube_id: str
    name: str


@dataclass(frozen=True)
class PipelineMapping:
    """Describes how a YouTube source maps to a Telegram destination."""

    telegram_channel_id: str
    streams: List[StreamConfig]

    @property
    def stream_ids(self) -> List[str]:
        return [stream.youtube_id for stream in self.streams]


@dataclass(frozen=True)
class AuthConfig:
    """Authentication tokens used by external services."""

    telegram_bot_token: str
    youtube_api_key: str
    allowed_user_ids: Set[str]


@dataclass(frozen=True)
class ApplicationConfig:
    """Aggregated configuration for the application."""

    log_level: str
    poll_seconds: int
    show_upcoming: bool
    max_consecutive_failures: int
    telegram_updates_poll_interval: float
    telegram_updates_timeout: float
    cache_path: Path
    auth: AuthConfig
    pipelines: List[PipelineMapping]

    @property
    def allowed_actor_ids(self) -> Set[str]:
        """Union of allowed user IDs and target Telegram channels."""

        allowed: Set[str] = set(self.auth.allowed_user_ids)
        for pipeline in self.pipelines:
            normalized = pipeline.telegram_channel_id.strip()
            if not normalized:
                continue
            allowed.add(normalized)
            if normalized.lstrip("-").isdigit():
                # Ensure we also allow the numeric form without leading zeros
                allowed.add(str(int(normalized)))
        return allowed


class ConfigurationError(ValueError):
    """Raised when the YAML configuration is invalid."""


def load_config(config_path: str | Path | None = None) -> ApplicationConfig:
    """Load application configuration from a YAML file.

    Args:
        config_path: Optional path to the YAML configuration. If omitted, the
            ``CONFIG_PATH`` environment variable is consulted, falling back to
            ``config.yaml``.

    Raises:
        ConfigurationError: If the configuration file is missing or malformed.
    """

    path = Path(config_path or os.getenv(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH)).expanduser()
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found at {path}")

    raw_payload = _load_yaml_text(path.read_text(encoding="utf-8"))

    if raw_payload is None:
        raw_payload = {}
    if not isinstance(raw_payload, dict):
        raise ConfigurationError("Top-level YAML document must be a mapping")

    return _build_config(raw_payload, base_path=path.parent)


def _build_config(payload: dict, *, base_path: Path) -> ApplicationConfig:
    poll_seconds = _coerce_int(payload.get("poll_seconds"), DEFAULT_POLL_SECONDS, "poll_seconds")
    show_upcoming = _coerce_bool(payload.get("show_upcoming"), DEFAULT_SHOW_UPCOMING, "show_upcoming")
    max_consecutive_failures = _coerce_int(
        payload.get("max_consecutive_failures"),
        DEFAULT_MAX_CONSECUTIVE_FAILURES,
        "max_consecutive_failures",
    )

    telegram_updates_poll_interval = _coerce_positive_float(
        payload.get("telegram_updates_poll_interval"),
        DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL,
        "telegram_updates_poll_interval",
    )
    telegram_updates_timeout = _coerce_positive_float(
        payload.get("telegram_updates_timeout"),
        min(float(poll_seconds), DEFAULT_TELEGRAM_UPDATES_TIMEOUT),
        "telegram_updates_timeout",
    )
    if telegram_updates_timeout > 50.0:
        logger.warning(
            "telegram_updates_timeout=%s exceeds Telegram API limit (50s); clamping to 50s",
            telegram_updates_timeout,
        )
        telegram_updates_timeout = 50.0

    cache_path_raw = str(payload.get("cache_path") or DEFAULT_CACHE_PATH).strip()
    cache_path = (base_path / cache_path_raw).expanduser()

    auth = _parse_auth(payload.get("auth"))
    pipelines = _parse_pipelines(payload.get("pipelines"))
    log_level = str(payload.get("log_level") or DEFAULT_LOG_LEVEL)

    return ApplicationConfig(
        log_level=log_level,
        poll_seconds=poll_seconds,
        show_upcoming=show_upcoming,
        max_consecutive_failures=max_consecutive_failures,
        telegram_updates_poll_interval=telegram_updates_poll_interval,
        telegram_updates_timeout=telegram_updates_timeout,
        cache_path=cache_path,
        auth=auth,
        pipelines=pipelines,
    )


def _load_yaml_text(text: str):
    if yaml is not None:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:  # pragma: no cover - PyYAML formatting errors are unlikely
            raise ConfigurationError(f"Failed to parse YAML configuration: {exc}") from exc

    return _fallback_safe_load(text)


def _fallback_safe_load(text: str):
    tokens = _tokenize_yaml(text)
    if not tokens:
        return {}

    document, index = _parse_tokens(tokens, 0, tokens[0][0])
    if index != len(tokens):
        raise ConfigurationError("Unexpected trailing content in YAML configuration")
    return document


def _tokenize_yaml(text: str) -> List[Tuple[int, str]]:
    tokens: List[Tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        tokens.append((indent, line))
    return tokens


def _parse_tokens(tokens: List[Tuple[int, str]], start_index: int, current_indent: int):
    mapping: dict = {}
    sequence: list = []
    index = start_index

    while index < len(tokens):
        indent, content = tokens[index]
        if indent < current_indent:
            break

        stripped = content.strip()
        if stripped.startswith("- "):
            if mapping:
                raise ConfigurationError("Cannot mix mappings and sequences in YAML content")
            item_content = stripped[2:].strip()
            index += 1

            if item_content and ":" in item_content:
                key, _, remainder = item_content.partition(":")
                value = _parse_scalar(remainder.strip()) if remainder.strip() else None
                nested = None
                if index < len(tokens) and tokens[index][0] > indent:
                    nested, index = _parse_tokens(tokens, index, tokens[index][0])

                entry = {key.strip(): value}
                if nested is not None:
                    if value is None:
                        entry[key.strip()] = nested
                    elif isinstance(nested, dict):
                        entry.update(nested)

                sequence.append(entry)
                continue

            if index < len(tokens) and tokens[index][0] > indent:
                nested, index = _parse_tokens(tokens, index, tokens[index][0])
                sequence.append(nested)
                continue

            sequence.append(_parse_scalar(item_content) if item_content else None)
            continue

        if sequence:
            raise ConfigurationError("Cannot mix mappings and sequences in YAML content")

        key, sep, remainder = stripped.partition(":")
        if not sep:
            raise ConfigurationError(f"Invalid line in configuration: {content}")

        remainder = remainder.strip()
        index += 1

        if remainder:
            mapping[key.strip()] = _parse_scalar(remainder)
            continue

        if index < len(tokens) and tokens[index][0] > indent:
            nested, index = _parse_tokens(tokens, index, tokens[index][0])
            mapping[key.strip()] = nested
        else:
            mapping[key.strip()] = None

    if sequence:
        return sequence, index
    return mapping, index


def _parse_scalar(raw_value: str):
    lower = raw_value.lower()
    if lower in {"true", "yes", "on"}:
        return True
    if lower in {"false", "no", "off"}:
        return False
    if lower in {"null", "none", "~"}:
        return None

    if (raw_value.startswith("\"") and raw_value.endswith("\"")) or (
        raw_value.startswith("'") and raw_value.endswith("'")
    ):
        return raw_value[1:-1]

    if raw_value.startswith("[") and raw_value.endswith("]"):
        inner = raw_value[1:-1].strip()
        if not inner:
            return []
        parts = [part.strip() for part in inner.split(",")]
        return [_parse_scalar(part.strip("'\"")) for part in parts]

    try:
        return int(raw_value)
    except ValueError:
        pass

    try:
        return float(raw_value)
    except ValueError:
        pass

    return raw_value


def _parse_auth(raw_auth: object) -> AuthConfig:
    if not isinstance(raw_auth, dict):
        raise ConfigurationError("'auth' section is required and must be a mapping")

    telegram_section = raw_auth.get("telegram") if isinstance(raw_auth.get("telegram"), dict) else {}
    youtube_section = raw_auth.get("youtube") if isinstance(raw_auth.get("youtube"), dict) else {}

    telegram_token = str(telegram_section.get("bot_token") or "").strip()
    if not telegram_token:
        raise ConfigurationError("auth.telegram.bot_token is required")

    youtube_api_key = str(youtube_section.get("api_key") or "").strip()
    if not youtube_api_key:
        raise ConfigurationError("auth.youtube.api_key is required")

    allowed_ids_raw: Sequence[str] = telegram_section.get("allowed_user_ids", [])  # type: ignore[assignment]
    allowed_user_ids: Set[str] = set()
    if isinstance(allowed_ids_raw, Sequence) and not isinstance(allowed_ids_raw, (str, bytes, bytearray)):
        for raw_id in allowed_ids_raw:
            cleaned = str(raw_id).strip()
            if cleaned:
                allowed_user_ids.add(cleaned)
    else:
        logger.warning("auth.telegram.allowed_user_ids must be a list; ignoring invalid value")

    return AuthConfig(
        telegram_bot_token=telegram_token,
        youtube_api_key=youtube_api_key,
        allowed_user_ids=allowed_user_ids,
    )


def _parse_pipelines(raw_pipelines: object) -> List[PipelineMapping]:
    if not isinstance(raw_pipelines, list) or not raw_pipelines:
        raise ConfigurationError("pipelines must be a non-empty list")

    pipelines: List[PipelineMapping] = []
    for index, raw_pipeline in enumerate(raw_pipelines):
        if not isinstance(raw_pipeline, dict):
            raise ConfigurationError(f"Pipeline at index {index} must be a mapping")

        channel_id = str(raw_pipeline.get("telegram_channel_id") or "").strip()
        if not channel_id:
            raise ConfigurationError(f"Pipeline at index {index} is missing telegram_channel_id")

        streams = _parse_streams(raw_pipeline.get("streams"), pipeline_index=index)
        pipelines.append(PipelineMapping(telegram_channel_id=channel_id, streams=streams))

    return pipelines


def _parse_streams(raw_streams: object, *, pipeline_index: int) -> List[StreamConfig]:
    if not isinstance(raw_streams, list) or not raw_streams:
        raise ConfigurationError(f"Pipeline at index {pipeline_index} must define a non-empty streams list")

    streams: List[StreamConfig] = []
    for stream_index, raw_stream in enumerate(raw_streams):
        youtube_id: str | None = None
        name: str | None = None

        if isinstance(raw_stream, dict):
            youtube_id = str(raw_stream.get("id") or raw_stream.get("youtube_id") or "").strip()
            name = raw_stream.get("name")
        elif isinstance(raw_stream, (list, tuple)):
            if not raw_stream:
                youtube_id = ""
            else:
                youtube_id = str(raw_stream[0]).strip()
                if len(raw_stream) > 1:
                    name = raw_stream[1]
        else:
            raise ConfigurationError(
                f"Stream at index {stream_index} in pipeline {pipeline_index} must be a mapping or list"
            )

        if not youtube_id:
            raise ConfigurationError(
                f"Stream at index {stream_index} in pipeline {pipeline_index} is missing a YouTube identifier"
            )

        name = str(name).strip() if name is not None else youtube_id
        streams.append(StreamConfig(youtube_id=youtube_id, name=name))

    return streams


def _coerce_int(raw_value: object, default: int, field: str) -> int:
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        logger.warning("Invalid integer for %s=%r; using %s", field, raw_value, default)
        return default
    return value


def _coerce_positive_float(raw_value: object, default: float, field: str) -> float:
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        logger.warning("Invalid float for %s=%r; using %s", field, raw_value, default)
        return default
    if value < 0:
        logger.warning("Negative value for %s=%s is not allowed; using %s", field, value, default)
        return default
    return value


def _coerce_bool(raw_value: object, default: bool, field: str) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    logger.warning("Invalid boolean for %s=%r; using %s", field, raw_value, default)
    return default


__all__ = [
    "ApplicationConfig",
    "AuthConfig",
    "ConfigurationError",
    "PipelineMapping",
    "StreamConfig",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_SHOW_UPCOMING",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL",
    "DEFAULT_TELEGRAM_UPDATES_TIMEOUT",
    "DEFAULT_CACHE_PATH",
    "DEFAULT_CONFIG_PATH",
    "CONFIG_PATH_ENV",
    "load_config",
]
