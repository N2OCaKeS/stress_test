import type { ApiSection } from "../types";

export const EXPORT: ApiSection = {
  id: "export",
  title: "CSV-экспорт",
  service: "loging",
  description:
    "Выгрузка audit-журнала за временное окно в виде CSV-файла (text/csv, " +
    "Content-Disposition: attachment). Колонки совпадают с полями GET /events; " +
    "details сериализуется компактным JSON'ом в последней колонке. Окно и " +
    "фильтры — те же, что у GET /events/stats: дефолт последние 24 часа, " +
    "явные from_time/to_time (полуоткрытый интервал [from, to)) либо сдвиг " +
    "window_hours, плюс severity/service/action/actor_id/target_id/status/" +
    "department_id/request_id. Не более 50000 строк на один экспорт: если под " +
    "фильтр попало больше, отдаются первые строки по возрастанию времени и " +
    "заголовок X-Export-Truncated: true — сузьте окно или фильтр. Доступ: " +
    "loging_admin / loging_reader (обе роли видят журнал cross-dept).",
  examples: [
    {
      id: "events-export-basic",
      title: "Экспорт за окно по умолчанию (последние 24 часа)",
      method: "GET",
      path: "/events/export",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "Без явного окна экспортируются события за последние 24 часа. Ответ — " +
        "CSV-файл (text/csv; charset=utf-8) с заголовочной строкой колонок и " +
        "строками событий, отсортированными по возрастанию времени. Имя файла в " +
        "Content-Disposition содержит границы окна. curl скачивает тело в " +
        "events.csv через -o; -H \"Accept: text/csv\" фиксирует ожидаемый тип.",
      notes:
        "Колонки CSV: id, timestamp, received_at, service, action, actor_id, " +
        "actor_type, username, department_id, target_id, target_type, status, " +
        "allowed, severity, request_id, details. Заголовок ответа " +
        "X-Export-Truncated = false, пока строк не больше 50000. Роль не в " +
        "allow-list → 403 INSUFFICIENT_ROLE; нет/неверный токен → 401.",
      curl: `TOKEN="{{TOKEN}}"

# -o пишет тело прямо в файл, -D выводит заголовки (для X-Export-Truncated)
curl -D - -o events.csv \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Accept: text/csv" \\
  "{{BASE_URL}}/api/logging/v1/events/export"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "text/csv"}

# stream=True не тянет весь CSV в память — пишем тело чанками в файл
with requests.get(
    f"{BASE_URL}/api/logging/v1/events/export",
    headers=headers,
    stream=True,
) as resp:
    resp.raise_for_status()
    print("content-type:", resp.headers["Content-Type"])
    # заголовок усечения читается до вычитки тела
    truncated = resp.headers.get("X-Export-Truncated") == "true"
    with open("events.csv", "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)

if truncated:
    print("экспорт усечён по лимиту 50000 строк — сузьте окно или фильтр")
else:
    print("выгружены все строки под фильтр")`,
    },
    {
      id: "events-export-filtered",
      title: "Экспорт с фильтром severity и явным окном",
      method: "GET",
      path: "/events/export",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "Фильтры сужают выборку перед выгрузкой (all-AND, как у GET /events). " +
        "Здесь — только severity=CRITICAL за окно: задаём window_hours (или пару " +
        "from_time/to_time в ISO 8601). Если from_time и to_time заданы оба — " +
        "window_hours игнорируется; если задан только один край — второй " +
        "достраивается сдвигом window_hours.",
      notes:
        "severity — один из TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL. " +
        "window_hours: 1..8784 (год), дефолт 24. Имена service/action " +
        "нормализуются на сервере (NFKC + lower), как при приёме событий. " +
        "X-Export-Truncated = true сигналит, что под фильтр попало больше " +
        "50000 строк и в файле — только первые по времени.",
      curl: `TOKEN="{{TOKEN}}"

# только CRITICAL за последние 6 часов, тело в critical.csv
curl -D - -o critical.csv \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Accept: text/csv" \\
  "{{BASE_URL}}/api/logging/v1/events/export?severity=CRITICAL&window_hours=6"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "text/csv"}
params = {
    "severity": "CRITICAL",
    "window_hours": 6,
    # либо явные границы окна вместо window_hours:
    # "from_time": "2026-06-13T00:00:00Z",
    # "to_time": "2026-06-14T00:00:00Z",
}

with requests.get(
    f"{BASE_URL}/api/logging/v1/events/export",
    headers=headers,
    params=params,
    stream=True,
) as resp:
    resp.raise_for_status()
    truncated = resp.headers.get("X-Export-Truncated") == "true"
    with open("critical.csv", "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)

print("content-type:", resp.headers["Content-Type"], "| truncated:", truncated)`,
    },
  ],
};
