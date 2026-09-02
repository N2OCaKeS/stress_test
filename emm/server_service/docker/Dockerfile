FROM python:3.12-slim

# Allta devpi package index
RUN printf '%s\n' '[global]' 'index-url = http://allta.devos.astralinux.ru:3141/root/release' 'trusted-host = allta.devos.astralinux.ru' > /etc/pip.conf && chmod 0644 /etc/pip.conf

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

EXPOSE 8002

# --proxy-headers + --forwarded-allow-ips=*:
# В prod server_service крутится за nginx/traefik/k8s-ingress. Без
# `--proxy-headers` uvicorn ВООБЩЕ не парсит `X-Forwarded-For` —
# `request.client.host` остаётся IP'шкой reverse-proxy. Это значит:
#   * audit-trail ip_address = IP nginx'а, не реального клиента;
#   * slowapi per-IP rate-limit считает все запросы как один источник
#     → один атакующий может «забрать» всю quota (DOS-самобан);
#   * `extract_client_ip` уважает TRUSTED_PROXY_IPS, но логика
#     работает ТОЛЬКО если uvicorn парсит X-Forwarded-For.
#
# `--forwarded-allow-ips=$FORWARDED_ALLOW_IPS` — env-переменная для строгих
# деплоев со whitelist'ом ingress-IP. Default fallback `*` оставлен для
# текущих стендов (k8s ingress IP может меняться, ставить allowlist руками
# непрактично) — но в проде с фиксированным набором ingress'ов рекомендуется
# выставлять `FORWARDED_ALLOW_IPS=10.0.0.0/8,192.168.0.0/16` или конкретные IP.
ENV FORWARDED_ALLOW_IPS=*

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port 8002 --proxy-headers --forwarded-allow-ips=$FORWARDED_ALLOW_IPS"]
