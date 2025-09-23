# ===== 1) build stage =====
FROM python:3.11-slim AS build

# Системные зависимости (библиотеки + компилятор если вдруг понадобится)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# объявим версии пакетов отдельно — кэш будет стабильнее
ARG PTB_VER=21.6
ARG AIOHTTP_VER=3.10.5

# создаём wheelhouse с зависимостями
RUN pip install --upgrade pip wheel
RUN pip wheel --no-cache-dir --wheel-dir=/wheels \
    python-telegram-bot==${PTB_VER} \
    aiohttp==${AIOHTTP_VER}

# ===== 2) runtime stage =====
FROM python:3.11-slim

# лёгкая оптимизация Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# создаём непривилегированного пользователя
RUN useradd -ms /bin/bash appuser
WORKDIR /app

# ставим только wheels (быстро и без dev-зависимостей)
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# копируем код
COPY tg_youtube_live_feed.py /app/tg_youtube_live_feed.py

# переключаемся на non-root
USER appuser

# переменные окружения читаются из docker-compose или docker run
# HEALTHCHECK (по желанию): проверяет, что процесс жив
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD pgrep -f "tg_youtube_live_feed.py" >/dev/null || exit 1

# основная команда
CMD ["python", "tg_youtube_live_feed.py"]

