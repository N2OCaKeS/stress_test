import json
import os

from app.db.migrations import run_migrations


bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
loglevel = "debug"


def on_starting(server):
    run_migrations()
    server.log.info("Alembic migrations applied.")

    data_dir = "/data"
    info_path = os.path.join(data_dir, "info.json")

    os.makedirs(data_dir, exist_ok=True)
    if not os.path.exists(info_path):
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

    server.log.info(f"DATA_DIR initialized: {data_dir}")
    server.log.info(f"INFO_PATH initialized: {info_path}")
