import type { ApiSection } from "../types";

export const SERVERS: ApiSection = {
  id: "servers",
  title: "Серверы",
  service: "server",
  description:
    "CRUD карточек серверов + busy-lease (захват/освобождение под тест) и " +
    "ручной OS-sync. Все эндпоинты под /api/server/v1/servers, требуют Bearer " +
    "и роль с соответствующим action в матрице (server, *, <action>). Сервера " +
    "изолированы по department: чужой сервер скрыт за 404, platform-админам " +
    "(account_admin / loging_admin) middleware отдаёт 403.",
  examples: [
    {
      id: "servers-list",
      title: "Список серверов (offset, legacy)",
      method: "GET",
      path: "{{BASE_URL}}/api/server/v1/servers",
      auth: "Bearer + (server, *, view)",
      description:
        "Возвращает страницу серверов своего department'а, отсортированных по " +
        "created_at DESC, id DESC. Без явного cursor=true / after endpoint остаётся " +
        "в legacy offset/limit-режиме с envelope {items, total, limit, offset}. " +
        "limit 1..500 (default 100), offset >= 0.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n\n' +
        'curl "$base_url/api/server/v1/servers?limit=50&offset=0" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/server/v1/servers",\n' +
        '    params={"limit": 50, "offset": 0},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, data["total"])\n' +
        'for s in data["items"]:\n' +
        '    print(s["id"], s["hostname"], s["busy_state"])',
      notes:
        "Ответ 200: {items, total, limit, offset}. items — массив ServerResponse " +
        "(id, hostname, ip_address, status, power_state, busy_state, storage, ...). " +
        "offset помечен DEPRECATED — на вставках в начало и глубоком offset стабильнее " +
        "cursor (см. соседний пример). Ошибки: 403 PERMISSION_DENIED (нет view), " +
        "403 SERVICE_ACCESS_DENIED (department без доступа к server_service), " +
        "403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED.",
    },
    {
      id: "servers-list-cursor",
      title: "Список серверов (cursor, рекомендуемый)",
      method: "GET",
      path: "{{BASE_URL}}/api/server/v1/servers",
      auth: "Bearer + (server, *, view)",
      description:
        "Keyset-пагинация. Первый запрос — cursor=true без after; envelope " +
        "{items, next_cursor, has_more}. next_cursor подставляется в ?after= " +
        "следующего запроса; null/has_more=false означает конец. Битый или чужой " +
        "after → 400 INVALID_CURSOR. Стабильнее offset на вставках в начало списка.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n\n' +
        '# первая страница\n' +
        'curl "$base_url/api/server/v1/servers?cursor=true&limit=50" \\\n' +
        '  -H "Authorization: Bearer $token"\n\n' +
        '# следующая страница: подставить next_cursor из ответа\n' +
        'curl "$base_url/api/server/v1/servers?after=<next_cursor>&limit=50" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'headers = {"Authorization": f"Bearer {token}"}\n\n' +
        '# пройти все страницы\n' +
        'after = None\n' +
        'while True:\n' +
        '    params = {"limit": 50, "cursor": "true"}\n' +
        '    if after:\n' +
        '        params["after"] = after\n' +
        '    r = requests.get(f"{base_url}/api/server/v1/servers", params=params, headers=headers)\n' +
        '    data = r.json()\n' +
        '    for s in data["items"]:\n' +
        '        print(s["id"], s["hostname"])\n' +
        '    if not data["has_more"]:\n' +
        '        break\n' +
        '    after = data["next_cursor"]',
      notes:
        "Ответ 200: {items, next_cursor, has_more}. Передача after автоматически " +
        "включает cursor-режим (cursor=true можно опустить, если after задан). " +
        "Ошибки: 400 INVALID_CURSOR (after не декодируется), 403 PERMISSION_DENIED / " +
        "SERVICE_ACCESS_DENIED / PLATFORM_ADMIN_BUSINESS_DATA_DENIED.",
    },
    {
      id: "servers-create",
      title: "Зарегистрировать сервер",
      method: "POST",
      path: "{{BASE_URL}}/api/server/v1/servers",
      auth: "Bearer + (server, *, create)",
      description:
        "Создаёт карточку сервера. Обязательны hostname (уникальный FQDN), " +
        "ip_address (UNIQUE) и department_id — он обязан совпадать со своим, " +
        "иначе 403 DEPARTMENT_ISOLATION. ssh_port по умолчанию 22. storage — " +
        "набор дисков (slot system/diskN, size_gb, is_system; не более одного " +
        "системного). Опциональный блок ipmi заводит BMC-контроллер атомарно " +
        "вместе с сервером. ID генерится сервером (srv_<uuid>).",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'dept="{{DEPARTMENT_ID}}"  # должен совпадать со своим\n\n' +
        'curl -X POST "$base_url/api/server/v1/servers" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\n' +
        '        \\"hostname\\": \\"node01.nt.local\\",\n' +
        '        \\"ip_address\\": \\"10.50.0.10\\",\n' +
        '        \\"department_id\\": \\"$dept\\",\n' +
        '        \\"ssh_port\\": 22,\n' +
        '        \\"storage\\": [\n' +
        '          {\\"slot\\": \\"system\\", \\"size_gb\\": 256, \\"model\\": \\"Samsung SSD\\"},\n' +
        '          {\\"slot\\": \\"disk1\\", \\"size_gb\\": 1024}\n' +
        '        ]\n' +
        '      }"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'dept = "{{DEPARTMENT_ID}}"  # должен совпадать со своим\n\n' +
        'body = {\n' +
        '    "hostname": "node01.nt.local",\n' +
        '    "ip_address": "10.50.0.10",\n' +
        '    "department_id": dept,\n' +
        '    "ssh_port": 22,\n' +
        '    "storage": [\n' +
        '        {"slot": "system", "size_gb": 256, "model": "Samsung SSD"},\n' +
        '        {"slot": "disk1", "size_gb": 1024},\n' +
        '    ],\n' +
        '}\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/server/v1/servers",\n' +
        '    json=body,\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json()["id"])',
      notes:
        "Ответ 201: ServerResponse. Прочие поля карточки опциональны (display_name, " +
        "mgmt_ip_address, serial_number, asset_tag, location, cpu_*, ram_total_mb, " +
        "network_interface_name, os_version_id). Idempotency-Key НЕ читается " +
        "(owner-decision): повтор ловится UNIQUE-конфликтом. Ошибки: " +
        "403 DEPARTMENT_ISOLATION (чужой dept), 403 PERMISSION_DENIED (нет create), " +
        "409 SERVER_DUPLICATE (hostname/ip/serial_number), 409 IPMI_DUPLICATE, " +
        "422 INVALID_OS_VERSION / валидация тела (дубль слота, >1 системного диска).",
    },
    {
      id: "servers-get",
      title: "Карточка сервера",
      method: "GET",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}",
      auth: "Bearer + (server, *, view)",
      description:
        "Полная карточка сервера по id. Видны только сервера своего department'а; " +
        "чужой/несуществующий сервер отдаётся как 404 (а не 403), чтобы не утечь " +
        "факт его существования.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl "$base_url/api/server/v1/servers/$server_id" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        's = r.json()\n' +
        'print(r.status_code, s["hostname"], s["status"], s["is_managed"])\n' +
        '# инвентарные поля — заполняются после inventory.sync\n' +
        'print(s["os_security_mode"], s["ram_total_mb"], s["ram_total_gb"])\n' +
        'print(s["network_interfaces"])  # список активных интерфейсов, без lo\n' +
        'for d in s["storage"]:\n' +
        '    print(d["slot"], d["size_gb"], d["is_system"], d["used_percent"])',
      notes:
        "Ответ 200: ServerResponse. Помимо базовой карточки несёт инвентарные " +
        "поля, снятые последней inventory.sync: os_security_mode (режим " +
        "безопасности Astra — Smolensk/Orel/Voronezh, UI склеивает с именем " +
        "версии), ram_total_mb и производное ram_total_gb, network_interfaces " +
        "(все активные интерфейсы без lo), а у каждого диска в storage — " +
        "is_system и used_percent/used_gb. Плюс busy_*, power_state, " +
        "is_managed/management_user/prepared_at, mgmt_ssh_public_key, " +
        "mgmt_creds_rotated_at, mgmt_creds_pending_apply. До первой " +
        "инвентаризации инвентарные поля пустые (null / []). Ошибки: " +
        "403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND (в т.ч. чужой department).",
    },
    {
      id: "servers-update",
      title: "Обновить карточку (PATCH)",
      method: "PATCH",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}",
      auth: "Bearer + (server, *, update)",
      description:
        "Частичное обновление: присылается только то, что меняется " +
        "(model_dump(exclude_unset=True)). Пустое тело → текущий объект без UPDATE. " +
        "storage: поле опущено — диски не трогаются; [] — все удаляются; список — " +
        "полная синхронизация под него. Менять hostname через PATCH нельзя — поля нет.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl -X PATCH "$base_url/api/server/v1/servers/$server_id" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"location\\": \\"DC1/rack5/u10\\", \\"cpu_cores\\": 16}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.patch(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}",\n' +
        '    json={"location": "DC1/rack5/u10", "cpu_cores": 16},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json()["location"])',
      notes:
        "Ответ 200: обновлённый ServerResponse. Обновляемые поля: display_name, " +
        "ip_address, mgmt_ip_address, ssh_port, os_version_id, cpu_*, ram_total_mb, " +
        "network_interface_name, serial_number, asset_tag, location, storage. " +
        "Ошибки: 403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND, " +
        "409 SERVER_DUPLICATE (ip/serial_number).",
    },
    {
      id: "servers-delete",
      title: "Удалить сервер (hard-delete)",
      method: "DELETE",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}",
      auth: "Bearer + (server, *, delete)",
      description:
        "Жёсткое удаление строки с каскадом на server_accounts, ipmi_controllers, " +
        "server_disks (ondelete=CASCADE). Восстановить нельзя — для soft-delete " +
        "сервер переводят в статус decommissioned через PATCH. Требует именно " +
        "action delete в матрице (отдельный от update).",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl -X DELETE "$base_url/api/server/v1/servers/$server_id" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.delete(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json())',
      notes:
        "Ответ 200: {\"ok\": true}. Ошибки: 403 PERMISSION_DENIED (нет delete), " +
        "404 SERVER_NOT_FOUND (в т.ч. чужой department).",
    },
    {
      id: "servers-busy-acquire",
      title: "Захватить сервер (busy-lease)",
      method: "POST",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}/busy",
      auth: "Bearer + (server, *, busy_acquire)",
      description:
        "Атомарный CAS-UPDATE: проходит только если busy_state сейчас free. " +
        "Помечает сервер занятым (busy_state=busy, busy_user_id=caller, " +
        "busy_since=now, busy_note из purpose / lease_until). Тело опционально. " +
        "lease_until носит информационный характер — auto-release не делается.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl -X POST "$base_url/api/server/v1/servers/$server_id/busy" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"purpose\\": \\"kernel-stress run\\", \\"lease_until\\": \\"2026-06-15T12:00:00Z\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}/busy",\n' +
        '    json={"purpose": "kernel-stress run", "lease_until": "2026-06-15T12:00:00Z"},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        's = r.json()\n' +
        'print(r.status_code, s["busy_state"], s["busy_user_id"])',
      notes:
        "Ответ 200: ServerResponse с busy_state=busy. Тело можно опустить " +
        "(оба поля опциональны). Конкурентный acquire: один получает 200, второй — " +
        "409 SERVER_ALREADY_BUSY без переписывания первого. Ошибки: " +
        "403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND, 409 SERVER_ALREADY_BUSY, " +
        "409 SERVER_DECOMMISSIONED.",
    },
    {
      id: "servers-busy-release",
      title: "Освободить сервер",
      method: "DELETE",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}/busy",
      auth: "Bearer + (server, *, busy_release)",
      description:
        "Атомарный UPDATE busy_state → free + обнуление busy_user_id/busy_since/" +
        "busy_note. Освобождать может любая роль с busy_release (обычно тот же " +
        "caller или admin отдела). Уже свободный сервер → 409 SERVER_NOT_BUSY " +
        "(сигнал рассинхрона у клиента).",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl -X DELETE "$base_url/api/server/v1/servers/$server_id/busy" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.delete(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}/busy",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json()["busy_state"])',
      notes:
        "Ответ 200: ServerResponse с busy_state=free. Ошибки: 403 PERMISSION_DENIED, " +
        "404 SERVER_NOT_FOUND, 409 SERVER_NOT_BUSY (сервер уже free).",
    },
    {
      id: "servers-os-sync",
      title: "Ручная смена os_version_id",
      method: "POST",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}/os-sync",
      auth: "Bearer + (server, *, os_sync)",
      description:
        "Прямое выставление servers.os_version_id + os_last_synced_at=now() без " +
        "запуска worker-inventory. Полезно, когда железо переустановили вручную " +
        "или нужен быстрый фикс. os_version_id=null сбрасывает версию. " +
        "Hardware-inventory через worker — отдельный flow (POST /inventory/sync).",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl -X POST "$base_url/api/server/v1/servers/$server_id/os-sync" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"os_version_id\\": \\"osv_...\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        '# os_version_id=None сбрасывает версию\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}/os-sync",\n' +
        '    json={"os_version_id": "osv_..."},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        's = r.json()\n' +
        'print(r.status_code, s["os_version_id"], s["os_last_synced_at"])',
      notes:
        "Ответ 200: ServerResponse с обновлённым os_version_id. id версии — из " +
        "группы OS-версий (osv_<...>). Ошибки: 403 PERMISSION_DENIED, " +
        "404 SERVER_NOT_FOUND, 422 INVALID_OS_VERSION (нет такой строки в os_versions).",
    },
    {
      id: "servers-astra-update",
      title: "Обновить ОС Astra до версии каталога (worker)",
      method: "POST",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}/astra-update",
      auth: "Bearer + (server, *, update)",
      description:
        "Публикует задачу server.astra_update: worker заходит на сервер по SSH " +
        "под управляющим пользователем (сервер обязан быть is_managed), " +
        "ПОЛНОСТЬЮ перезаписывает /etc/apt/sources.list репозиториями выбранной " +
        "версии ОС (OsVersion.repositories) и гонит apt update && astra-update " +
        "-A -T -r. Тело — {os_version_id} (целевая версия каталога, osv_...). " +
        "На время обновления сервер уходит в busy_state='updating' — системная " +
        "блокировка отбивает ЛЮБЫЕ другие операции над сервером (prepare / " +
        "inventory / power / account-ops / console) с 409 SERVER_UPDATING, " +
        "включая владельца брони и админа. Блокировку снимает callback worker'а; " +
        "при успехе сервер привязывается к целевой версии и запускается " +
        "inventory.sync. Асинхронный: 202 с task_id.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl -X POST "$base_url/api/server/v1/servers/$server_id/astra-update" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"os_version_id\\": \\"osv_...\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}/astra-update",\n' +
        '    json={"os_version_id": "osv_..."},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json())  # 202 {"task_id": "tsk_...", "status": "queued"}',
      notes:
        "202, не 200: задача только поставлена. Одноразовая (max_attempts=1) — " +
        "упавшее обновление автоматически не повторяется. Ошибки: " +
        "403 PERMISSION_DENIED, 404 SERVER_NOT_FOUND / OS_VERSION_NOT_FOUND, " +
        "409 SERVER_DECOMMISSIONED / SERVER_UPDATING (обновление уже идёт) / " +
        "SERVER_RESERVED / PREPARE_REQUIRED / OS_VERSION_NO_REPOSITORIES " +
        "(у версии пустой список репозиториев — sources.list переписать нечем), " +
        "503 WORKER_UNREACHABLE. Исход обновления worker постит через " +
        "internal-callback (server.astra_updated).",
      asyncTask: true,
    },
    {
      id: "servers-mgmt-creds-rotate",
      title: "Ротация управляющих кред сервера (worker)",
      method: "POST",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}/management-credentials/rotate",
      auth: "Bearer + (server, *, update)",
      description:
        "Публикует задачу server.rotate_management_creds. server_service генерит " +
        "новую Ed25519-пару + пароль управляющего пользователя, переносит текущий " +
        "материал в previous_mgmt_* (анти-локаут) и ставит " +
        "mgmt_creds_pending_apply=true. Worker заходит ДЕЙСТВУЮЩИМ ключом, ставит " +
        "новый pubkey + chpasswd, проверяет вход новым ключом и колбэчит " +
        "/internal/.../management-credentials/applied — server_service снимает " +
        "pending и зануляет previous. Сервер обязан быть prepared (is_managed). " +
        "Асинхронный: 202 с task_id.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        'curl -X POST "$base_url/api/server/v1/servers/$server_id/management-credentials/rotate" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}/management-credentials/rotate",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json())  # 202 {"task_id": "tsk_...", "status": "queued"}',
      notes:
        "202: задача поставлена, ротация подтверждается callback'ом worker'а. " +
        "Повтор, пока предыдущая ротация не подтверждена (mgmt_creds_pending_apply " +
        "ещё true), отбивается 409 MGMT_ROTATION_PENDING — дождись применения на " +
        "боксе (в карточке сервера mgmt_creds_pending_apply вернётся в false) или " +
        "сбрось зависшую ротацию перед новой. Прочие ошибки: 403 PERMISSION_DENIED, " +
        "404 SERVER_NOT_FOUND, 409 SERVER_DECOMMISSIONED / PREPARE_REQUIRED " +
        "(сервер не prepared) / TASK_IDEMPOTENT_CONFLICT, 503 " +
        "WORKER_REDIS_UNAVAILABLE / WORKER_UNREACHABLE. Аудит — CRITICAL.",
      asyncTask: true,
    },
    {
      id: "servers-drift",
      title: "Drift-сводка сервера",
      method: "GET",
      path: "{{BASE_URL}}/api/server/v1/servers/{server_id}/drift",
      auth: "Bearer + (server, *, view_drift)",
      description:
        "Агрегирует события server_account.drift_detected за окно [since, now] " +
        "(по умолчанию last 24h) из loging_service. Drift'ы эмитит инвентаризация " +
        "OS-пользователей: БД-истина не перетирается, но расхождения поднимают " +
        "WARNING для оператора. Удобно как «что недавно случилось с сервером».",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'server_id="srv_..."\n\n' +
        '# окно по умолчанию — последние 24 часа\n' +
        'curl "$base_url/api/server/v1/servers/$server_id/drift" \\\n' +
        '  -H "Authorization: Bearer $token"\n\n' +
        '# явное начало окна\n' +
        'curl "$base_url/api/server/v1/servers/$server_id/drift?since=2026-06-01T00:00:00Z" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'server_id = "srv_..."\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/server/v1/servers/{server_id}/drift",\n' +
        '    params={"since": "2026-06-01T00:00:00Z"},  # опционально\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, len(data["drifts"]), "truncated:", data["truncated"])',
      notes:
        "Ответ 200: {server_id, since, drifts[], truncated}. since — ISO 8601 UTC; " +
        "при отсутствии берётся now-24h. truncated=true означает переполнение окна " +
        "(событий больше, чем влезло). Ошибки: 403 PERMISSION_DENIED (нет view_drift), " +
        "404 SERVER_NOT_FOUND, 503 LOGING_SERVICE_UNAVAILABLE / " +
        "LOGING_SERVICE_NOT_CONFIGURED / LOGING_SERVICE_AUTH_FAILED.",
    },
  ],
};
