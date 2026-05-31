# smrouter

Social Media Router — a small bot that mirrors YouTube live/upcoming broadcasts
into Telegram channels. Each pipeline polls one or more YouTube channels and
keeps a live "dashboard" message up to date in its Telegram destination.

## Requirements

- Python 3.11+
- A Telegram bot token and a YouTube Data API key

## Configuration

The bot is configured through a YAML file (defaults to `config.yaml`).

1. Copy `config.example.yaml` to `config.yaml` and replace the placeholders.
2. Provide authentication tokens under `auth`:
   - `auth.telegram.bot_token` — Telegram bot token.
   - `auth.telegram.allowed_user_ids` — optional list of Telegram user IDs allowed to use `/start`.
   - `auth.youtube.api_key` — YouTube Data API key.
3. Define `pipelines`: each entry ties a Telegram channel to its own list of YouTube streams/channels. Streams are defined as two-element lists `[<youtube_id>, <friendly_name>]` (or objects with `id`/`name`).
4. Runtime behavior (polling intervals, cache path, etc.) can be tuned with the top-level keys shown in the example file.

Set the `CONFIG_PATH` environment variable if you want to point the app to a different YAML file.

> `config.yaml` and the cache files contain real credentials/state and are git-ignored — never commit them.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py                     # reads ./config.yaml by default
# or point at another file:
CONFIG_PATH=/path/to/config.yaml python main.py
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests also run in CI on every push and pull request (see
`.github/workflows/tests.yml`).
