import pytest

from genti.config import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_POLL_SECONDS,
    DEFAULT_SHOW_UPCOMING,
    DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL,
    DEFAULT_TELEGRAM_UPDATES_TIMEOUT,
    ConfigurationError,
    load_config,
)


def test_load_config_parses_multiple_pipelines(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
log_level: DEBUG
poll_seconds: 120
show_upcoming: true
auth:
  telegram:
    bot_token: "123:ABC"
    allowed_user_ids:
      - "111"
      - "222"
  youtube:
    api_key: "yt-key"
pipelines:
  - telegram_channel_id: "-100123"
    streams:
      - ["UC1", "Channel One"]
      - id: "UC2"
        name: "Channel Two"
  - telegram_channel_id: "@news"
    streams:
      - ["UC3", "News"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.poll_seconds == 120
    assert config.show_upcoming is True
    assert config.auth.telegram_bot_token == "123:ABC"
    assert config.auth.youtube_api_key == "yt-key"
    assert len(config.pipelines) == 2

    first_pipeline = config.pipelines[0]
    assert first_pipeline.stream_ids == ["UC1", "UC2"]
    assert [stream.name for stream in first_pipeline.streams] == ["Channel One", "Channel Two"]

    allowed = config.allowed_actor_ids
    assert "-100123" in allowed
    assert "@news" in allowed
    assert "111" in allowed and "222" in allowed


def test_load_config_applies_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
auth:
  telegram:
    bot_token: "token"
  youtube:
    api_key: "api"
pipelines:
  - telegram_channel_id: "-1001"
    streams:
      - ["UC1", "One"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.poll_seconds == DEFAULT_POLL_SECONDS
    assert config.show_upcoming == DEFAULT_SHOW_UPCOMING
    assert config.max_consecutive_failures == DEFAULT_MAX_CONSECUTIVE_FAILURES
    assert config.telegram_updates_poll_interval == DEFAULT_TELEGRAM_UPDATES_POLL_INTERVAL
    assert config.telegram_updates_timeout == DEFAULT_TELEGRAM_UPDATES_TIMEOUT


def test_load_config_requires_pipelines(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
auth:
  telegram:
    bot_token: "token"
  youtube:
    api_key: "api"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        load_config(config_path)
