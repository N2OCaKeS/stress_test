"""Operator-facing CLI для server_worker'а.

Точка входа — `python -m src.cli` (см. `__main__.py`). Команды живут в
отдельных модулях этого пакета: например, `outbox.py` для ручного
re-attempt'а строк `audit_outbox` из DLQ.

Назначение CLI — операторские ручки на случай инцидентов, когда обычного
оркестратора (taskiq broker / server_service internal-API) недостаточно:
например, надо ткнуть конкретный outbox-row в shell'е на работающем
worker-pod'е. Сюда же со временем переедут другие ad-hoc операции
(re-queue task'и, форсированный sweep и т.п.).
"""
