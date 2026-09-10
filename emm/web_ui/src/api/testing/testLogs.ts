/**
 * Тонкие обёртки над `testing_service` `/queue-items/{id}/log*` — чтение и
 * экспорт логов прогонов (§2.6, §8.4 плана миграции). Открыто любому
 * аутентифицированному актору, как и остальные read-пути этого сервиса.
 *
 * `WS .../log/stream` (живой лог, §8.6) — отдельный модуль `@/api/testing/logStream`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/test_logs.py`.
 */

import { ApiError, API_BASE_URL, apiGet } from "@/api/client";
import { getAccessToken } from "@/api/tokenStore";
import type { ApiErrorPayload } from "@/api/auth/types";
import type {
  TestingPaginatedResponse,
  TestLogSegment,
} from "@/api/testing/types";

const BASE = "/testing/v1";

const CONTENT_DISPOSITION_FILENAME = /filename="?([^"]+)"?/i;

/** Диапазон текста лога — оба поля опциональны (офсеты `byte_offset_*` сегмента). */
export interface LogRange {
  from?: number;
  to?: number;
}

/** Результат `getTestLogText`/`downloadTestLog` — текст + имя файла из `Content-Disposition`. */
export interface TestLogTextResult {
  text: string;
  filename: string;
}

/**
 * `GET /queue-items/{id}/log` — весь текст лога или диапазон по офсетам
 * (`from`/`to`). Прямой `fetch` (не `apiGet`), т.к. ответ — не JSON
 * (`text/plain`) и нужен `Content-Disposition` для имени файла (§8.3:
 * `<stand>_<testname>_<os_version>_<kernel>_<date>.log`). Невалидный диапазон
 * (`from>to`, за пределами длины текста) — 422.
 */
export async function getTestLogText(
  queueItemId: string,
  range: LogRange = {},
): Promise<TestLogTextResult> {
  const params = new URLSearchParams();
  if (range.from !== undefined) params.set("from", String(range.from));
  if (range.to !== undefined) params.set("to", String(range.to));
  const qs = params.toString();
  const url = `${API_BASE_URL}${BASE}/queue-items/${queueItemId}/log${qs ? `?${qs}` : ""}`;

  const headers: Record<string, string> = { Accept: "text/plain" };
  const access = getAccessToken();
  if (access) headers["Authorization"] = `Bearer ${access}`;

  const res = await fetch(url, { method: "GET", headers, credentials: "same-origin" });

  if (!res.ok) {
    let payload: Partial<ApiErrorPayload> = {};
    try {
      payload = (await res.json()) as Partial<ApiErrorPayload>;
    } catch {
      // на ошибке эндпоинт всё равно отдаёт JSON-envelope; пустой payload —
      // fallback на случай нестандартного тела.
    }
    throw new ApiError(res.status, payload);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = CONTENT_DISPOSITION_FILENAME.exec(disposition);
  const filename = match?.[1] ?? `${queueItemId}.log`;
  const text = await res.text();
  return { text, filename };
}

/**
 * Скачивает лог в браузере (`<a download>` + `URL.createObjectURL`) — тот же
 * приём, что и `exportEvents` в `@/api/loging/events`. Использует
 * `getTestLogText` под капотом, поэтому диапазон работает так же.
 */
export async function downloadTestLog(
  queueItemId: string,
  range: LogRange = {},
): Promise<TestLogTextResult> {
  const result = await getTestLogText(queueItemId, range);
  const blob = new Blob([result.text], { type: "text/plain;charset=utf-8" });
  const objectUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = result.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
  return result;
}

/** Параметры `GET /queue-items/{id}/log/segments` — фильтр по статусу + пагинация. */
export interface ListLogSegmentsQuery {
  status?: "OK" | "CHANGED" | "FATAL" | string;
  limit?: number;
  offset?: number;
}

/**
 * `GET /queue-items/{id}/log/segments` — метаданные сегментов (чекпоинты/
 * команды) без самого текста — офсеты для перехода к диапазону через
 * `getTestLogText`.
 */
export function listLogSegments(
  queueItemId: string,
  query: ListLogSegmentsQuery = {},
): Promise<TestingPaginatedResponse<TestLogSegment>> {
  return apiGet<TestingPaginatedResponse<TestLogSegment>>(
    `${BASE}/queue-items/${queueItemId}/log/segments`,
    { query: { ...query } },
  );
}
