# genti
YT 2 TG

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

## Build the image

```bash
docker compose build
```

## Start the stack

```bash
docker compose up -d
```

## Tail the logs

```bash
docker compose logs -f
```

## Stop the stack

```bash
docker compose down
```
