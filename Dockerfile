# ===== 1) build stage =====
FROM python:3.11-slim AS build

# System dependencies (libraries and a compiler if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Declare package versions to keep the build cache stable
ARG PTB_VER=21.6
ARG AIOHTTP_VER=3.10.5
ARG PYYAML_VER=6.0.2

# Build a wheelhouse with dependencies
RUN pip install --upgrade pip wheel
RUN pip wheel --no-cache-dir --wheel-dir=/wheels \
    python-telegram-bot==${PTB_VER} \
    aiohttp==${AIOHTTP_VER} \
    PyYAML==${PYYAML_VER}

# ===== 2) runtime stage =====
FROM python:3.11-slim

# Lightweight Python optimisations
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create an unprivileged user
RUN useradd -ms /bin/bash appuser
WORKDIR /app

# Install only wheels (fast and without dev dependencies)
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy the application code
COPY . /app

# Switch to the non-root user
USER appuser

# Environment variables are provided via docker-compose or docker run
# Optional HEALTHCHECK to ensure the process stays alive
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD pgrep -f "main.py" >/dev/null || exit 1

# Default command
CMD ["python", "main.py"]

