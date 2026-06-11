/**
 * Thin wrappers для loging_service `/services` (реестр сервисов и action'ов).
 *
 * Backend под `/api/logging/v1/services`. Read-доступ — `loging_admin` /
 * `loging_reader`. Регистрация каталога (`POST /services/{svc}/events`) —
 * service-to-service канал, из UI не вызывается.
 *
 * Source of truth: loging_service/src/api/v1/endpoints/services.py
 */

import { apiGet } from "@/api/client";
import type {
  ServiceEventsResponse,
  ServiceListResponse,
} from "@/api/loging/types";

/** `GET /api/logging/v1/services` — сервисы с агрегатом событий. */
export function listServices(): Promise<ServiceListResponse> {
  return apiGet<ServiceListResponse>("/logging/v1/services");
}

/** `GET /api/logging/v1/services/{service}/events` — каталог action'ов сервиса. */
export function listServiceEvents(
  service: string,
  query: { limit?: number; offset?: number } = {},
): Promise<ServiceEventsResponse> {
  return apiGet<ServiceEventsResponse>(
    `/logging/v1/services/${service}/events`,
    { query: { limit: query.limit, offset: query.offset } },
  );
}
