FROM python:3.12-slim

# Allta devpi package index
RUN pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release && pip config --global set global.trusted-host allta.devos.astralinux.ru

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Install dependencies into system Python (no venv) so `python -m alembic`
# and `uvicorn` in CMD work without `poetry run`.
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.4

# Copy dependency files first (better layer caching). poetry.lock is generated
# on first build if absent; commit it back to the repo after the first run.
COPY pyproject.toml ./
COPY poetry.lock* ./

# Install only production dependencies (no dev group)
RUN poetry install --only main --no-root

COPY src ./src
COPY alembic.ini ./

EXPOSE 8003

# --proxy-headers + --forwarded-allow-ips=*:
# В prod secret_service крутится за nginx/traefik/k8s-ingress. Без
# `--proxy-headers` uvicorn не парсит `X-Forwarded-For` — `request.client.host`
# остаётся IP'шкой reverse-proxy, audit-trail и slowapi per-IP rate-limit
# теряют точность. `FORWARDED_ALLOW_IPS=*` — default fallback для стендов
# с динамическим ingress-IP; в проде с фиксированным списком ingress'ов
# рекомендуется выставлять конкретные CIDR'ы.
ENV FORWARDED_ALLOW_IPS=*

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port 8003 --proxy-headers --forwarded-allow-ips=$FORWARDED_ALLOW_IPS"]
