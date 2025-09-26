# genti
YT 2 TG

# собрать образ
docker compose build

# запустить
docker compose up -d

# смотреть логи
docker compose logs -f

# остановить
docker compose down

## переменные окружения

- `POLL_SECONDS` — частота опроса YouTube и обновления дашборда (по умолчанию 90 секунд).
- `TELEGRAM_UPDATES_POLL_INTERVAL` — пауза между запросами `getUpdates` Telegram (по умолчанию равна `POLL_SECONDS`).
- `TELEGRAM_UPDATES_TIMEOUT` — таймаут long polling Telegram, автоматически ограничивается 50 секундами.

