import type { ApiSection } from "../types";

export const INVENTORY_TASKS: ApiSection = {
  id: "inventory-tasks",
  title: "Инвентаризация, пакеты, задачи",
  service: "server",
  description:
    "Сбор фактов о сервере (hardware, OS-пользователи, установленные пакеты) " +
    "идёт через worker: endpoint только ставит задачу в очередь и сразу " +
    "отвечает 202 с {task_id, status:\"queued\"}. Результат забирается " +
    "отдельным GET /tasks/{id} — там же финальный status и result. " +
    "Полный сценарий dispatch → poll → result разобран в разделе «Сценарии».",
  examples: [
    {
      id: "inventory-sync",
      title: "Запустить hardware-инвентаризацию (inventory.sync)",
      method: "POST",
      path: "/api/server/v1/servers/{server_id}/inventory/sync",
      auth: "Bearer + (server, inventory_trigger)",
      description:
        "Публикует задачу inventory.sync — worker заходит на сервер по SSH и " +
        "снимает OS/kernel/CPU/disks (os-release, lscpu, lsblk), затем постит " +
        "факты обратно через internal-callback. Асинхронный: ответ 202 с " +
        "task_id, сами факты — в task.result после завершения (см. GET " +
        "/tasks/{id}). На неуправляемом (is_managed=false) сервере worker " +
        "ходит под аккаунтом сервера: либо передай account_id, либо " +
        "server_service возьмёт дефолтный привязанный (первый с сохранённым " +
        "паролем). Привязок нет — 422 ACCOUNT_REQUIRED. На управляемом " +
        "account_id игнорируется (вход по ключу под management_user). " +
        "inventory_trigger — то же право, что у инвентаризации пользователей.",
      curl:
        'curl -X POST "{{BASE_URL}}/api/server/v1/servers/srv_e98.../inventory/sync" \\\n' +
        '  -H "Authorization: Bearer {{TOKEN}}"\n' +
        '# опц. конкретный аккаунт для self-сессии на неуправляемом сервере:\n' +
        '#   ...?account_id=acc_65ee...\n' +
        '# ответ: 202 {"task_id":"tsk_...","status":"queued"}',
      python:
        "import requests\n\n" +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_e98..."\n\n' +
        "r = requests.post(\n" +
        '    f"{base_url}/api/server/v1/servers/{server_id}/inventory/sync",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        "    # params={\"account_id\": \"acc_65ee...\"},  # опц.\n" +
        ")\n" +
        "print(r.status_code, r.json())  # 202 {'task_id': 'tsk_...', 'status': 'queued'}",
      notes:
        "202, не 200: задача только поставлена. Facts появятся в result " +
        "целевой task после её завершения. Errors: 403 PERMISSION_DENIED, " +
        "404 SERVER_NOT_FOUND, 409 SERVER_DECOMMISSIONED / " +
        "TASK_IDEMPOTENT_CONFLICT, 422 ACCOUNT_REQUIRED / ACCOUNT_NOT_LINKED, " +
        "503 WORKER_UNREACHABLE.",
    },
    {
      id: "users-inventory",
      title: "Инвентаризация OS-пользователей (users.inventory)",
      method: "POST",
      path: "/api/server/v1/servers/{server_id}/users/inventory",
      auth: "Bearer + (server, inventory_trigger)",
      description:
        "Публикует задачу users.inventory — worker читает getent passwd / " +
        "группы / sudoers, фильтрует системных по UID_MIN и постит список " +
        "обратно; server_service сверяет его с server_accounts. Асинхронный: " +
        "202 с task_id, результат (список пользователей) — в task.result. " +
        "Право то же, что у hardware-инвентаризации — отдельного " +
        "users_inventory_trigger нет. account_id резолвится так же, как у " +
        "inventory.sync.",
      curl:
        'curl -X POST "{{BASE_URL}}/api/server/v1/servers/srv_e98.../users/inventory" \\\n' +
        '  -H "Authorization: Bearer {{TOKEN}}"\n' +
        '# ответ: 202 {"task_id":"tsk_...","status":"queued"}',
      python:
        "import requests\n\n" +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_e98..."\n\n' +
        "r = requests.post(\n" +
        '    f"{base_url}/api/server/v1/servers/{server_id}/users/inventory",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ")\n" +
        "print(r.status_code, r.json())  # 202 queued\n" +
        "# result после завершения: {'users': [{'login','uid','shell','has_sudo',...}], 'user_count': N}",
      notes:
        "Reconcile с server_accounts делает server_service по callback'у " +
        "worker'а. Errors совпадают с inventory.sync (включая 422 " +
        "ACCOUNT_REQUIRED / ACCOUNT_NOT_LINKED).",
    },
    {
      id: "installed-packages",
      title: "Live-список установленных пакетов (installed_packages.list)",
      method: "POST",
      path: "/api/server/v1/servers/{server_id}/installed-packages?pattern=bash",
      auth: "Bearer + (server, view)",
      description:
        "Публикует задачу installed_packages.list — worker идёт на сервер по " +
        "SSH и выполняет dpkg-query (Debian/Astra/Ubuntu), rpm -qa (RHEL) или " +
        "apk (Alpine) с shell-glob паттерном. В БД ничего не пишется (live " +
        "truth). Асинхронный: 202 с task_id, результат " +
        "{packages:[{name,version}], package_manager, count} — в task.result. " +
        "pattern — shell glob (bash, linux-image*, *-dev), не regex; по " +
        "умолчанию * (все пакеты). Право — обычный (server, view).",
      curl:
        '# pattern=bash — точечный запрос версии пакета\n' +
        'curl -X POST "{{BASE_URL}}/api/server/v1/servers/srv_e98.../installed-packages?pattern=bash" \\\n' +
        '  -H "Authorization: Bearer {{TOKEN}}"\n' +
        '# ответ: 202 {"task_id":"tsk_...","status":"queued"}',
      python:
        "import requests\n\n" +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_e98..."\n' +
        'pattern = "bash"\n\n' +
        "r = requests.post(\n" +
        '    f"{base_url}/api/server/v1/servers/{server_id}/installed-packages",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        '    params={"pattern": pattern},\n' +
        ")\n" +
        "print(r.status_code, r.json())  # 202 queued\n" +
        "# result после завершения: {'packages': [{'name': 'bash', 'version': '5.3.3-r1'}], 'count': 1, 'package_manager': 'apk'}",
      notes:
        "Только безопасные glob-символы [A-Za-z0-9._-+*?[]] — иначе 400 " +
        "INVALID_PATTERN. Менеджер пакетов worker определяет сам. Errors: " +
        "400 INVALID_PATTERN, 403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND, " +
        "409 SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT, 503 " +
        "WORKER_UNREACHABLE.",
    },
    {
      id: "tasks-list",
      title: "Список worker-задач (с фильтрами и пагинацией)",
      method: "GET",
      path: "/api/server/v1/tasks",
      auth: "Bearer + (task, view)",
      description:
        "Страница задач из очереди worker'а, отсортированных по времени " +
        "постановки (новые сверху). Тело — список TaskRead, общее число под " +
        "фильтром — в заголовке X-Total-Count. Фильтры: status " +
        "(queued/running/succeeded/failed/cancelled), kind (task_kind, напр. " +
        "installed_packages.list), server_id. Пагинация: limit (1..200, " +
        "default 50) + offset. Caller видит только задачи серверов своего " +
        "отдела; platform-админам (account_admin/loging_admin) вход запрещён.",
      curl:
        '# последние 20 задач\n' +
        'curl "{{BASE_URL}}/api/server/v1/tasks?limit=20" \\\n' +
        '  -H "Authorization: Bearer {{TOKEN}}"\n\n' +
        '# только succeeded по конкретному kind\n' +
        'curl "{{BASE_URL}}/api/server/v1/tasks?status=succeeded&kind=installed_packages.list" \\\n' +
        '  -H "Authorization: Bearer {{TOKEN}}"',
      python:
        "import requests\n\n" +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        "r = requests.get(\n" +
        '    f"{base_url}/api/server/v1/tasks",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        '    params={"status": "succeeded", "kind": "installed_packages.list", "limit": 20},\n' +
        ")\n" +
        'print(r.status_code, r.headers.get("X-Total-Count"))\n' +
        'for t in r.json():\n' +
        '    print(t["id"], t["kind"], t["status"])',
      notes:
        "result в листинге усечён до summary; полный result — в GET " +
        "/tasks/{id}. Errors: 403 PERMISSION_DENIED / " +
        "PLATFORM_ADMIN_BUSINESS_DATA_DENIED.",
    },
    {
      id: "tasks-get",
      title: "Деталь задачи (статус + полный result)",
      method: "GET",
      path: "/api/server/v1/tasks/{task_id}",
      auth: "Bearer + (task, view)",
      description:
        "Возвращает TaskRead с полным result и last_error. Главная ручка для " +
        "получения результата асинхронной операции: poll'им её, пока status не " +
        "станет терминальным (succeeded / failed / cancelled), затем читаем " +
        "result. Доступ — (task, view) + dept-visibility (чужой отдел " +
        "маскируется под 404).",
      curl:
        'curl "{{BASE_URL}}/api/server/v1/tasks/tsk_4053..." \\\n' +
        '  -H "Authorization: Bearer {{TOKEN}}"',
      python:
        "import requests\n\n" +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'task_id = "tsk_4053..."\n\n' +
        "r = requests.get(\n" +
        '    f"{base_url}/api/server/v1/tasks/{task_id}",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ")\n" +
        "task = r.json()\n" +
        'print(task["status"], task["last_error"])\n' +
        'if task["status"] == "succeeded":\n' +
        '    print(task["result"])',
      notes:
        "Терминальные статусы: succeeded / failed / cancelled. На failed " +
        "смотри last_error. Errors: 403 PERMISSION_DENIED, 404 TASK_NOT_FOUND " +
        "(нет row либо cross-dept).",
    },
    {
      id: "tasks-cancel",
      title: "Отменить pending/running задачу",
      method: "POST",
      path: "/api/server/v1/tasks/{task_id}/cancel",
      auth: "Bearer + (task, cancel)",
      description:
        "Помечает задачу cancelled. Pending — пропускается перед запуском; " +
        "running — graceful: текущий stage доживает, следующий не стартует " +
        "(force-kill нет). Тело опционально несёт {reason} — фиксируется в " +
        "audit. Доступ — (task, cancel), по умолчанию только роль admin. " +
        "Системные задачи (heartbeat/sweep/cleanup) отменяет только " +
        "account_admin.",
      curl:
        'curl -X POST "{{BASE_URL}}/api/server/v1/tasks/tsk_4053.../cancel" \\\n' +
        '  -H "Authorization: Bearer {{TOKEN}}" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d \'{"reason": "отменено оператором"}\'',
      python:
        "import requests\n\n" +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'task_id = "tsk_4053..."\n\n' +
        "r = requests.post(\n" +
        '    f"{base_url}/api/server/v1/tasks/{task_id}/cancel",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        '    json={"reason": "отменено оператором"},\n' +
        ")\n" +
        'print(r.status_code, r.json())  # {"status": "cancelled", "previous_status": ...}',
      notes:
        "Терминальную задачу отменить нельзя — 409 TASK_NOT_CANCELLABLE. " +
        "Errors: 403 PERMISSION_DENIED / SYSTEM_TASK_ADMIN_REQUIRED, 404 " +
        "TASK_NOT_FOUND, 409 TASK_NOT_CANCELLABLE.",
    },
  ],
};
