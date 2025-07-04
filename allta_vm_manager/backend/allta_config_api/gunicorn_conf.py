import os
import json



# ========== gunicorn ==========
bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
loglevel = "debug"

def on_starting(server):

    DATA_DIR = "/data"
    INFO_PATH = os.path.join(DATA_DIR, "info.json")
    if not os.path.exists(INFO_PATH):
        with open(INFO_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

    server.log.info(f"DATA_DIR initialized: {DATA_DIR}")
    server.log.info(f"INFO_PATH initialized: {INFO_PATH}")