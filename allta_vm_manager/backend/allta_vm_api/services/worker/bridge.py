import os
import json
import logging
import threading
import redis

# from tasks import (
#     task_server_init,
#     task_vm_create, task_vm_base_create, task_vm_update,
#     task_vm_start, task_vm_stop, task_vm_delete,
#     task_vm_astra_update,
#     task_snapshot_create, task_snapshot_delete, task_snapshot_revert,
# )
from tasks.server import task_server_init, task_server_remove
from tasks.vm import task_vm_astra_update, task_vm_base_create, task_vm_create, task_vm_delete, task_vm_start, task_vm_stop, task_vm_update
from tasks.snapshot import task_snapshot_create, task_snapshot_delete, task_snapshot_revert
from utils.config import settings
log = logging.getLogger(__name__)

REDIS_URL       = settings.BROKER_URL
INBOX_KEY       = settings.QUEUE_KEY
Q_SERVER        = f"{INBOX_KEY}:server"
Q_VM            = f"{INBOX_KEY}:vm"
Q_SNAPSHOT      = f"{INBOX_KEY}:snapshot"
CELERY_QUEUE    = settings.CELERY_QUEUE

DISPATCH = {
    "server.init":     (task_server_init, Q_SERVER),
    "server.remove":   (task_server_remove, Q_SERVER),
    "vm.create":       (task_vm_create, Q_VM),
    "vm.base_create":  (task_vm_base_create, Q_VM),
    "vm.update":       (task_vm_update, Q_VM),
    "vm.start":        (task_vm_start, Q_VM),
    "vm.stop":         (task_vm_stop, Q_VM),
    "vm.delete":       (task_vm_delete, Q_VM),
    "vm.astra_update": (task_vm_astra_update, Q_VM),
    "snapshot.create": (task_snapshot_create, Q_SNAPSHOT),
    "snapshot.delete": (task_snapshot_delete, Q_SNAPSHOT),
    "snapshot.revert": (task_snapshot_revert, Q_SNAPSHOT),
}

def _route_key_for_operation(op: str) -> str | None:
    _, q = DISPATCH.get(op, (None, None))
    return q

def _task_for_operation(op: str):
    t, _ = DISPATCH.get(op, (None, None))
    return t

def _router_loop():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    log.info("Router: INBOX=%s -> [%s, %s, %s]", INBOX_KEY, Q_SERVER, Q_VM, Q_SNAPSHOT)
    while True:
        key, raw = r.brpop(INBOX_KEY)
        try:
            env = json.loads(raw)
            op = (env.get("operation") or "").strip()
            q  = _route_key_for_operation(op)
            if not q:
                log.warning("Router: unknown op=%s, drop. env=%s", op, env)
                continue
            r.lpush(q, json.dumps(env))
            log.info("Router: routed op=%s to %s (task_id=%s)", op, q, env.get("task_id"))
        except Exception:
            log.exception("Router: failed to handle message from %s: %r", key, raw)

def _dispatcher_loop():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    prio = [Q_SERVER, Q_VM, Q_SNAPSHOT]
    log.info("Dispatcher: BRPOP priority %s", prio)
    while True:
        key, raw = r.brpop(prio)
        try:
            env = json.loads(raw)
            op  = (env.get("operation") or "").strip()
            task = _task_for_operation(op)
            if not task:
                log.warning("Dispatcher: unknown op=%s", op)
                continue
            task.apply_async(kwargs={"envelope": env})
            log.info("Dispatcher: enqueued Celery task for op=%s from %s (task_id=%s)", op, key, env.get("task_id"))
        except Exception:
            log.exception("Dispatcher: failed on %s payload: %r", key, raw)

def consume_loop():
    threading.Thread(target=_router_loop, daemon=True).start()
    threading.Thread(target=_dispatcher_loop, daemon=True).start()