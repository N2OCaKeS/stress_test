FROM python:3.12-slim

# Allta devpi package index
RUN pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release && pip config --global set global.trusted-host allta.devos.astralinux.ru

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.4

COPY pyproject.toml poetry.lock ./

RUN poetry install --only main --no-root

COPY src ./src

EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
