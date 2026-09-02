import type { ApiFlow } from "../types";

export const LOGING_FLOWS: ApiFlow[] = [
  {
    id: "flow-audit-investigation",
    title: "Аудит-расследование (фильтр → stats → export)",
    description:
      "Связка трёх read-ручек loging_service для разбора инцидента: сначала " +
      "находим критичные события за окно (GET /events?severity=CRITICAL&" +
      "window_hours=24), потом снимаем сводку по тому же окну " +
      "(GET /events/stats) — сколько всего, как раскладывается по severity / " +
      "сервисам / исходам, — и в конце выгружаем те же критичные события в CSV " +
      "(GET /events/export?severity=CRITICAL) для приложения к отчёту. Окно и " +
      "фильтры (severity / service / action / actor / status / department / " +
      "request_id) у всех трёх ручек одинаковые, поэтому фильтр, заданный на " +
      "первом шаге, переносится в stats и export без изменений. Доступ ко всем " +
      "шагам — loging_admin / loging_reader.",
    steps: [
      {
        title: "1. Получить токен",
        description:
          "loging_service не выдаёт токены сам — POST /token проксирует " +
          "OAuth2 password flow в auth_service. Логинимся read-ролью " +
          "(loging_admin или loging_reader) и забираем access_token; дальше " +
          "ходим с ним в Authorization: Bearer. Тело — form-urlencoded " +
          "(username/password), как требует OAuth2.",
        curl:
          'curl -s -X POST "{{BASE_URL}}/api/logging/v1/token" \\\n' +
          '  -H "Content-Type: application/x-www-form-urlencoded" \\\n' +
          '  -d "username=loging_admin1&password=1234"\n' +
          "# из ответа берём access_token и подставляем в {{TOKEN}} ниже",
        python:
          "import requests\n\n" +
          'base_url = "{{BASE_URL}}"\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/logging/v1/token",\n' +
          '    data={"username": "loging_admin1", "password": "1234"},\n' +
          ")\n" +
          "r.raise_for_status()\n" +
          'token = r.json()["access_token"]\n' +
          'headers = {"Authorization": f"Bearer {token}"}\n' +
          "print(r.status_code)  # 200",
      },
      {
        title: "2. Найти критичные события за 24 часа",
        description:
          "GET /events с severity=CRITICAL и window_hours=24. Возвращает " +
          "постраничный список событий (по убыванию времени) под фильтр. По " +
          "умолчанию total = null (точный COUNT по журналу дорог) — для " +
          "навигации смотрим has_more; нужно точное число под фильтр — добавь " +
          "include_total=true. Тут видно конкретные записи: какой сервис, " +
          "action, actor, status, request_id — отсюда тянется ниточка " +
          "расследования. Фильтры комбинируются (service, action, actor_id, " +
          "status, request_id, from/to).",
        curl:
          'curl -s "{{BASE_URL}}/api/logging/v1/events?severity=CRITICAL&window_hours=24&limit=50" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}"\n' +
          "# нужно точное число под фильтр — добавь &include_total=true",
        python:
          "r = requests.get(\n" +
          '    f"{base_url}/api/logging/v1/events",\n' +
          "    headers=headers,\n" +
          '    params={"severity": "CRITICAL", "window_hours": 24, "limit": 50},\n' +
          ")\n" +
          "r.raise_for_status()\n" +
          "page = r.json()\n" +
          'for e in page["items"]:\n' +
          '    print(e["timestamp"], e["service"], e["action"], e["status"], e["request_id"])\n' +
          '# total=None по умолчанию; для навигации — page["has_more"]',
      },
      {
        title: "3. Снять сводку по тому же окну",
        description:
          "GET /events/stats с тем же окном/фильтром даёт агрегаты одним " +
          "запросом: total + by_severity + by_service + by_status + " +
          "фактические границы окна (from_time/to_time, UTC). Это масштаб " +
          "картины — сколько всего критичного, какие сервисы и исходы дают " +
          "основной вклад. severity тут можно убрать, чтобы увидеть полную " +
          "раскладку по уровням за окно. Уровни, под которые в окне ничего не " +
          "попало, в by_severity отсутствуют (нулей сервис не подставляет). " +
          "truncated=true означает, что GROUP BY упёрся в statement_timeout и " +
          "счётчики неполны — тогда сузь окно.",
        curl:
          "# полная раскладка по severity за окно (без фильтра severity):\n" +
          'curl -s "{{BASE_URL}}/api/logging/v1/events/stats?window_hours=24" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}"',
        python:
          "r = requests.get(\n" +
          '    f"{base_url}/api/logging/v1/events/stats",\n' +
          "    headers=headers,\n" +
          '    params={"window_hours": 24},\n' +
          ")\n" +
          "r.raise_for_status()\n" +
          "stats = r.json()\n" +
          'print("total:", stats["total"])\n' +
          'print("by_severity:", stats["by_severity"])  # {"CRITICAL": 617, "INFO": 810, ...}\n' +
          'print("by_service:", stats["by_service"])\n' +
          'print("by_status:", stats["by_status"])\n' +
          'print("window:", stats["from_time"], "->", stats["to_time"])\n' +
          'if stats["truncated"]:\n' +
          '    print("счётчики неполны — сузь окно")',
      },
      {
        title: "4. Выгрузить критичные события в CSV для отчёта",
        description:
          "GET /events/export с тем же фильтром (severity=CRITICAL, " +
          "window_hours=24) отдаёт события за окно как CSV-файл " +
          "(Content-Disposition: attachment, имя файла несёт границы окна). " +
          "Колонки совпадают с полями /events; details сериализуется " +
          "компактным JSON'ом в последней колонке. Cap — MAX_EXPORT_ROWS строк " +
          "на экспорт: если под фильтр попало больше, отдаются первые (по " +
          "возрастанию времени) и заголовок X-Export-Truncated: true — тогда " +
          "сузь окно или фильтр и выгрузи по частям. Это финальный артефакт " +
          "расследования — прикладывается к отчёту по инциденту.",
        curl:
          'curl -s -D - "{{BASE_URL}}/api/logging/v1/events/export?severity=CRITICAL&window_hours=24" \\\n' +
          '  -H "Authorization: Bearer {{TOKEN}}" \\\n' +
          "  -o audit-critical.csv\n" +
          "# проверь заголовок X-Export-Truncated в выводе -D -;\n" +
          "# true → под фильтр попало больше MAX_EXPORT_ROWS, сузь окно",
        python:
          "r = requests.get(\n" +
          '    f"{base_url}/api/logging/v1/events/export",\n' +
          "    headers=headers,\n" +
          '    params={"severity": "CRITICAL", "window_hours": 24},\n' +
          ")\n" +
          "r.raise_for_status()\n" +
          'with open("audit-critical.csv", "wb") as fh:\n' +
          "    fh.write(r.content)\n" +
          'truncated = r.headers.get("X-Export-Truncated") == "true"\n' +
          '_msg = "сохранено; усечено по cap-у" if truncated else "сохранено целиком"\n' +
          "print(_msg)\n" +
          "# truncated=True → сузь окно/фильтр и выгрузи по частям",
      },
    ],
    notes:
      "Окно у stats/export задаётся через window_hours (дефолт 24) либо парой " +
      "from_time/to_time (ISO 8601, UTC) — если заданы оба края, window_hours " +
      "игнорируется. Те же фильтры применимы ко всем трём шагам, поэтому " +
      "достаточно подобрать их на /events, а в stats/export перенести как есть. " +
      "Доступ ограничен loging_admin / loging_reader; account_admin / " +
      "department_admin к чтению аудита не допускаются (нужен read отделу — " +
      "выдаётся отдельная loging_reader).",
  },
];
