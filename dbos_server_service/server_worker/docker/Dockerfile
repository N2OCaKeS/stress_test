FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.4

COPY pyproject.toml ./
COPY poetry.lock* ./

RUN poetry install --only main --no-root

COPY src ./src
COPY alembic.ini ./

# Default command: taskiq worker. docker-compose overrides this to run
# `alembic upgrade head && taskiq worker ...` so migrations are applied on boot.
CMD ["taskiq", "worker", "src.main:broker", "--workers", "2"]
