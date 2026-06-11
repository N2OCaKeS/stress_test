/**
 * Thin wrappers для loging_service `/events` (чтение журнала аудита).
 *
 * Backend под `/api/logging/v1/events`. Ingest (`POST /events`) — это
 * service-to-service канал по `SERVICE_API_KEY`, из UI он не вызывается;
 * здесь только read-путь для `loging_admin` / `loging_reader`.
 *
 * Source of truth: loging_service/src/api/v1/endpoints/events.py
 */

import { apiGet } from "@/api/client";
import type { EventListResponse, ListEventsQuery } from "@/api/loging/types";

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
