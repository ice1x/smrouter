# genti
YT 2 TG

# Build the image
docker compose build

# Start the stack
docker compose up -d

# Tail the logs
docker compose logs -f

# Stop the stack
docker compose down

## Environment variables

- `POLL_SECONDS` — how frequently to poll YouTube and refresh the dashboard (default: 90 seconds).
- `TELEGRAM_UPDATES_POLL_INTERVAL` — pause between Telegram `getUpdates` requests (default: `POLL_SECONDS`).
- `TELEGRAM_UPDATES_TIMEOUT` — Telegram long polling timeout; automatically clamped to 50 seconds.

