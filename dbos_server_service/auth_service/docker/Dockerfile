FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Prevent poetry from creating a virtualenv inside the container
ENV POETRY_VENV_IN_PROJECT=false
ENV POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==2.3.4

# Copy dependency files first (better layer caching)
COPY pyproject.toml poetry.lock ./

# Install only production dependencies (no dev group)
RUN poetry install --only main --no-root

COPY src ./src

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
