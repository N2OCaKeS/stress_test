# Alembic migrations

Apply migrations:

```bash
cd loging_service
PYTHONPATH=. alembic upgrade head
```

Create a new migration after model changes:

```bash
cd loging_service
PYTHONPATH=. alembic revision --autogenerate -m "description"
```
