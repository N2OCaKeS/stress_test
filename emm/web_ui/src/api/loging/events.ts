/**
 * Thin wrappers для loging_service `/events` (чтение журнала аудита).
 *
 * Backend под `/api/logging/v1/events`. Ingest (`POST /events`) — это
 * service-to-service канал по `SERVICE_API_KEY`, из UI он не вызывается;
 * здесь только read-путь для `loging_admin` / `loging_reader`.
 *
 * Source of truth: loging_service/src/api/v1/endpoints/events.py
 */

import { ApiError, API_BASE_URL, apiGet } from "@/api/client";
import { getAccessToken } from "@/api/tokenStore";
import type {
  ApiErrorPayload,
} from "@/api/auth/types";
import type {
  EventListResponse,
  EventStatsQuery,
  EventStatsResponse,
  ListEventsQuery,
} from "@/api/loging/types";

/**
 * `GET /api/logging/v1/events` — постранично, `timestamp DESC`.
 *
 * Все поля `query` мапятся 1:1 на query-params backend'а. `status`
 * передаётся как `status` (backend читает его через alias). Пустые/undefined
 * значения `apiGet` отбрасывает, поэтому полупустой фильтр-объект безопасен.
 */
export function listEvents(
  query: ListEventsQuery = {},
): Promise<EventListResponse> {
  return apiGet<EventListResponse>("/logging/v1/events", {
    query: {
      department_id: query.department_id,
      service: query.service,
      severity: query.severity,
      action: query.action,
      actor_id: query.actor_id,
      target_id: query.target_id,
      status: query.status,
      request_id: query.request_id,
      from_time: query.from_time,
      to_time: query.to_time,
      limit: query.limit,
      offset: query.offset,
      include_total: query.include_total,
    },
  });
}

/**
 * `GET /api/logging/v1/events/stats` — агрегаты за окно.
 *
 * Те же auth/фильтры, что и у `/events` (`loging_admin` / `loging_reader`).
 * `window_hours` дефолтится на backend'е (24), но если переданы
 * `from_time`/`to_time` — окно строится по ним. Пустые поля `apiGet` отбросит.
 */
export function getEventStats(
  query: EventStatsQuery = {},
): Promise<EventStatsResponse> {
  return apiGet<EventStatsResponse>("/logging/v1/events/stats", {
    query: {
      department_id: query.department_id,
      service: query.service,
      severity: query.severity,
      action: query.action,
      actor_id: query.actor_id,
      target_id: query.target_id,
      status: query.status,
      request_id: query.request_id,
      from_time: query.from_time,
      to_time: query.to_time,
      window_hours: query.window_hours,
    },
  });
}

/** Результат экспорта CSV: blob уже скачан, плюс мета для уведомления. */
export interface ExportResult {
  /** Имя файла, под которым сохранён CSV (из `Content-Disposition`). */
  filename: string;
  /** Backend упёрся в кап 50k строк (`X-Export-Truncated: true`). */
  truncated: boolean;
}

const CONTENT_DISPOSITION_FILENAME = /filename="?([^"]+)"?/i;

function buildExportQuery(query: EventStatsQuery): string {
  const params = new URLSearchParams();
  const entries: Array<[string, string | number | undefined]> = [
    ["department_id", query.department_id],
    ["service", query.service],
    ["severity", query.severity],
    ["action", query.action],
    ["actor_id", query.actor_id],
    ["target_id", query.target_id],
    ["status", query.status],
    ["request_id", query.request_id],
    ["from_time", query.from_time],
    ["to_time", query.to_time],
    ["window_hours", query.window_hours],
  ];
  for (const [k, v] of entries) {
    if (v === undefined || v === null || v === "") continue;
    params.append(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

/**
 * `GET /api/logging/v1/events/export` — выгрузка CSV.
 *
 * Backend отвечает `text/csv` + `Content-Disposition: attachment`. Здесь
 * прямой `fetch` (а не `apiGet`), потому что нужны заголовки ответа — имя файла
 * и `X-Export-Truncated`. Скачивание триггерится через `URL.createObjectURL` +
 * скрытый `<a download>`. На не-2xx парсим стандартный envelope в `ApiError`,
 * чтобы вызывающий код мог отдать его в `apiErrMsg`.
 */
export async function exportEvents(
  query: EventStatsQuery = {},
): Promise<ExportResult> {
  const url = `${API_BASE_URL}/logging/v1/events/export${buildExportQuery(query)}`;
  const headers: Record<string, string> = { Accept: "text/csv" };
  const access = getAccessToken();
  if (access) headers["Authorization"] = `Bearer ${access}`;

  const res = await fetch(url, {
    method: "GET",
    headers,
    credentials: "same-origin",
  });

  if (!res.ok) {
    let payload: Partial<ApiErrorPayload> = {};
    try {
      payload = (await res.json()) as Partial<ApiErrorPayload>;
    } catch {
      // CSV-эндпоинт на ошибке всё равно отдаёт JSON-envelope; пустой payload
      // оставляем как fallback на случай нестандартного тела.
    }
    throw new ApiError(res.status, payload);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = CONTENT_DISPOSITION_FILENAME.exec(disposition);
  const filename = match?.[1] ?? "audit-export.csv";
  const truncated = res.headers.get("X-Export-Truncated") === "true";

  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }

  return { filename, truncated };
}


export async function listEventFilterOptions(kind: "department" | "actor" | "target") {
  const items: { value: string; label: string }[] = [];
  for (let offset = 0; ; offset += 500) {
    const page = await apiGet<{ items: typeof items; has_more: boolean }>("/logging/v1/events/filter-options", { query: { kind, offset, limit: 500 } });
    items.push(...page.items);
    if (!page.has_more) return items;
  }
}
