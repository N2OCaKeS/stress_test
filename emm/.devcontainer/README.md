# Global Dev Container

This dev container is shared by the whole `dbos_server_manager` repository.

Included:

- `Python 3.12`
- `Node.js LTS`
- `postgresql-client`
- build tools for Python packages
- dedicated PostgreSQL service

Forwarded ports:

- `8000` — backend services
- `5432` — PostgreSQL
- `3000` — frontend dev server
- `5173` — frontend dev server

Behavior:

- PostgreSQL starts automatically with the dev container
- Python dependencies for `auth_service` are installed automatically if `requirements.txt` exists
- frontend dependencies are installed automatically if `web_settings/package.json` exists
- Alembic migrations are applied automatically for each service where `alembic.ini` exists

Typical commands:

```bash
cd auth_service
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
cd web_settings
npm run dev -- --host 0.0.0.0
```
