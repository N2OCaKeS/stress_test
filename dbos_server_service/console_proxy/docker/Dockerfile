FROM python:3.12-slim AS vendor
# Отдельный слой тянет пинованную статику noVNC + spice-html5 в static/assets.
# Всё self-hosted: в рантайме CSP='self' и ничего не грузится с CDN.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tar ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY static ./static
COPY fetch_vendor.sh ./fetch_vendor.sh
RUN chmod +x fetch_vendor.sh && ./fetch_vendor.sh

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.4

COPY pyproject.toml ./
COPY poetry.lock* ./
RUN poetry install --only main --no-root

COPY src ./src
COPY --from=vendor /build/static ./static

EXPOSE 8085
CMD ["python", "-m", "src.main"]
