import type { ApiFlow } from "../types";

export const SERVER_FLOWS: ApiFlow[] = [
  {
    id: "flow-package-version",
    title: "Версия пакета на сервере (dispatch → poll → result)",
    description:
      "Канонический async-сценарий server_service. Операции probe'а сервера " +
      "не выполняются синхронно: endpoint ставит задачу в очередь worker'а и " +
      "сразу отдаёт 202 с task_id, а сам результат приходит позже. Поэтому " +
      "после dispatch'а опрашиваем GET /tasks/{task_id}, пока статус не " +
      "станет терминальным (succeeded / failed / cancelled), и только потом " +
      "читаем result. Здесь — узнаём версию установленного пакета (bash) " +
      "через installed_packages.list.",
    steps: [
      {
        title: "1. Получить токен (login)",
        description:
          "Логинимся в auth_service по username/password и забираем " +
          "access_token. Дальше ходим с ним в server_service в заголовке " +
          "Authorization: Bearer.",
        curl:
          'curl -s -X POST "{{BASE_URL}}/api/auth/v1/login" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d \'{"username":"dep_admin1","password":"1234"}\'\n' +
          '# из ответа берём access_token и подставляем в {{TOKEN}} ниже',
        python:
          "import requests, time\n\n" +
          'base_url = "{{BASE_URL}}"\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/auth/v1/login",\n' +
          '    json={"username": "dep_admin1", "password": "1234"},\n' +
          ")\n" +
          'token = r.json()["access_token"]\n' +
          'headers = {"Authorization": f"Bearer {token}"}\n' +
          "print(r.status_code)",
      },
      {
        title: "2. Поставить задачу installed_packages.list",
        description:
          "POST на /installed-packages с pattern=bash. Endpoint публикует " +
          "задачу installed_packages.list в worker и отвечает 202 — в теле " +
          "{task_id, status:\"queued\"}. Это ещё НЕ результат: запоминаем " +
          "task_id для опроса. pattern — shell glob (bash, linux-image*).",
        curl:
          'server_id="srv_e98..."\n' +
          'pattern="bash"\n\n' +
          'curl -s -X POST \\\n' +
          '  "{{BASE_URL}}/api/server/v1/servers/$server_id/installed-packages?pattern=$pattern" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}"\n' +
          '# ответ: 202 {"task_id":"tsk_...","status":"queued"} — запомни task_id',
        python:
          'server_id = "srv_e98..."\n' +
          'pattern = "bash"\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/server/v1/servers/{server_id}/installed-packages",\n' +
          "    headers=headers,\n" +
          '    params={"pattern": pattern},\n' +
          ")\n" +
          'assert r.status_code == 202, r.text\n' +
          'task_id = r.json()["task_id"]\n' +
          "print(r.status_code, task_id)  # 202 tsk_...",
      },
      {
        title: "3. Опрашивать GET /tasks/{task_id} до терминального статуса",
        description:
          "Worker выполняет задачу асинхронно. Дёргаем GET /tasks/{task_id} в " +
          "цикле с небольшой паузой, пока status не станет одним из " +
          "терминальных: succeeded / failed / cancelled. На failed разбираем " +
          "last_error. Цикл стоит ограничить по числу попыток, чтобы не " +
          "крутиться вечно.",
        curl:
          'task_id="tsk_..."\n\n' +
          '# повторять запрос с паузой, пока .status не станет\n' +
          '# succeeded / failed / cancelled:\n' +
          'curl -s "{{BASE_URL}}/api/server/v1/tasks/$task_id" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}"\n' +
          '# например в цикле:\n' +
          '#   while true; do\n' +
          '#     status=$(curl -s ".../tasks/$task_id" -H "Authorization: Bearer {{TOKEN}}" \\\n' +
          '#       | python3 -c "import sys,json;print(json.load(sys.stdin)[\'status\'])")\n' +
          '#     [ "$status" = succeeded ] || [ "$status" = failed ] && break\n' +
          '#     sleep 2\n' +
          '#   done',
        python:
          'terminal = {"succeeded", "failed", "cancelled"}\n' +
          "task = None\n" +
          "for _ in range(30):\n" +
          "    task = requests.get(\n" +
          '        f"{base_url}/api/server/v1/tasks/{task_id}",\n' +
          "        headers=headers,\n" +
          "    ).json()\n" +
          '    if task["status"] in terminal:\n' +
          "        break\n" +
          "    time.sleep(2)\n" +
          'print(task["status"])  # succeeded\n' +
          'if task["status"] == "failed":\n' +
          '    raise RuntimeError(task["last_error"])',
      },
      {
        title: "4. Разобрать result.packages → версия пакета",
        description:
          "У succeeded-задачи в result лежит {packages:[{name,version}], " +
          "count, package_manager}. Достаём версию интересующего пакета. На " +
          "seed-сервере (Alpine) installed_packages.list отдаёт apk-пакеты, " +
          "bash приходит с версией вида 5.3.3-r1.",
        curl:
          '# из тела succeeded-задачи берём result.packages:\n' +
          '# {\n' +
          '#   "status": "succeeded",\n' +
          '#   "result": {\n' +
          '#     "packages": [{"name": "bash", "version": "5.3.3-r1"}],\n' +
          '#     "count": 1,\n' +
          '#     "package_manager": "apk"\n' +
          '#   }\n' +
          '# }\n' +
          'curl -s "{{BASE_URL}}/api/server/v1/tasks/$task_id" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}" \\\n' +
          '  | python3 -c "import sys,json; r=json.load(sys.stdin)[\'result\']; [print(p[\'name\'], p[\'version\']) for p in r[\'packages\']]"',
        python:
          'packages = task["result"]["packages"]\n' +
          "by_name = {p[\"name\"]: p[\"version\"] for p in packages}\n" +
          'print("bash version:", by_name.get("bash"))  # 5.3.3-r1\n' +
          'print("package manager:", task["result"]["package_manager"])  # apk',
      },
    ],
    notes:
      "Тот же dispatch → poll → result паттерн у всех worker-операций " +
      "server_service (inventory.sync, users.inventory, power.status, " +
      "account.provision/rotate). Меняется только endpoint dispatch'а и " +
      "форма result. На неуправляемом сервере worker'у нужен аккаунт для " +
      "self-сессии — передай ?account_id=... либо положись на дефолтный " +
      "привязанный (422 ACCOUNT_REQUIRED, если привязок нет).",
  },
  {
    id: "flow-inventory-sync",
    title: "Hardware-инвентаризация (inventory.sync → poll → facts)",
    description:
      "Тот же async-паттерн, что и у пакетов, но для сбора фактов о железе и " +
      "ОС. inventory.sync — worker по SSH снимает os-release / kernel / CPU / " +
      "disks и кладёт их в result задачи (а также постит в server_service " +
      "через internal-callback). Сценарий: dispatch → poll → читаем " +
      "result.facts.",
    steps: [
      {
        title: "1. Поставить задачу inventory.sync",
        description:
          "POST на /inventory/sync. Ответ 202 с task_id. На неуправляемом " +
          "сервере account_id резолвится автоматически (дефолтный привязанный " +
          "аккаунт) либо передаётся явно; на управляемом — вход по ключу. " +
          "Право — (server, inventory_trigger).",
        curl:
          'server_id="srv_e98..."\n\n' +
          'curl -s -X POST "{{BASE_URL}}/api/server/v1/servers/$server_id/inventory/sync" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}"\n' +
          '# ответ: 202 {"task_id":"tsk_...","status":"queued"}',
        python:
          'server_id = "srv_e98..."\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/server/v1/servers/{server_id}/inventory/sync",\n' +
          "    headers=headers,\n" +
          ")\n" +
          'assert r.status_code == 202, r.text\n' +
          'task_id = r.json()["task_id"]\n' +
          "print(r.status_code, task_id)",
      },
      {
        title: "2. Опрашивать задачу до терминального статуса",
        description:
          "Как и в сценарии с пакетами: poll'им GET /tasks/{task_id} с " +
          "паузой, пока status не станет succeeded / failed / cancelled.",
        curl:
          'task_id="tsk_..."\n\n' +
          '# в цикле с паузой, пока .status терминальный:\n' +
          'curl -s "{{BASE_URL}}/api/server/v1/tasks/$task_id" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}"',
        python:
          'terminal = {"succeeded", "failed", "cancelled"}\n' +
          "task = None\n" +
          "for _ in range(30):\n" +
          "    task = requests.get(\n" +
          '        f"{base_url}/api/server/v1/tasks/{task_id}",\n' +
          "        headers=headers,\n" +
          "    ).json()\n" +
          '    if task["status"] in terminal:\n' +
          "        break\n" +
          "    time.sleep(2)\n" +
          'print(task["status"])  # succeeded',
      },
      {
        title: "3. Разобрать result.facts",
        description:
          "У succeeded-задачи result.facts несёт собранные срезы: os " +
          "(os-release: ID/NAME/VERSION_ID), kernel, cpu, disks. На утилитах, " +
          "которых на хосте нет (lscpu/lsblk на минимальном Alpine), " +
          "соответствующий срез приходит с полем error — это не валит задачу " +
          "целиком (status остаётся succeeded).",
        curl:
          '# из result.facts.os достаём дистрибутив и версию:\n' +
          'curl -s "{{BASE_URL}}/api/server/v1/tasks/$task_id" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}" \\\n' +
          '  | python3 -c "import sys,json; os=json.load(sys.stdin)[\'result\'][\'facts\'][\'os\']; print(os.get(\'PRETTY_NAME\'), os.get(\'VERSION_ID\'))"',
        python:
          'facts = task["result"]["facts"]\n' +
          'os_info = facts["os"]\n' +
          'print(os_info.get("PRETTY_NAME"), os_info.get("VERSION_ID"))\n' +
          '# срезы, которых нет на хосте, придут с ключом "error":\n' +
          'if "error" not in facts["cpu"]:\n' +
          '    print(facts["cpu"])',
      },
    ],
    notes:
      "Сырые facts остаются в task.result для диагностики, а нормализованные " +
      "уходят в server_service через internal-callback (submit_inventory_facts) — " +
      "поэтому даже при ошибке submit'а сами факты видны в задаче. " +
      "users.inventory работает идентично, только result несёт {users:[...], " +
      "user_count}.",
  },
];
