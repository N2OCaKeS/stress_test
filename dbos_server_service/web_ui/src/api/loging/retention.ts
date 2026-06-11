/**
 * Thin wrappers для loging_service `/retention` (срок хранения логов).
 *
 * Backend под `/api/logging/v1/retention`. Доступ — только `loging_admin`
 * (router-level `require_admin`). `PUT` всегда отдаёт 200 (новая row), `DELETE`
 * идемпотентен (гасит весь активный набор, 204).
 *
 * Ручного запуска ротации из API нет — sweep идёт фоном 00:00 MSK.
 *
 * Source of truth: loging_service/src/api/v1/endpoints/retention.py
 */

import { apiDelete, apiGet, apiPut } from "@/api/client";
import type {
  RetentionPolicy,
  RetentionPolicyPutRequest,
} from "@/api/loging/types";

/**
 * `GET /api/logging/v1/retention` — активная политика или `null`.
 *
 * Под filtered-режимом активным может быть набор строк (severity×service);
 * GET возвращает только представительскую строку (`get_active`).
 */
export function getRetention(): Promise<RetentionPolicy | null> {
  return apiGet<RetentionPolicy | null>("/logging/v1/retention");
}

/**
 * `PUT /api/logging/v1/retention` — задать/заменить политику.
 *
 * `retain_days` обязателен, [30, 3650]. Прежний активный набор гасится в той
 * же транзакции. `loging_service` в `service_filter` → 422.
 */
export function setRetention(
  body: RetentionPolicyPutRequest,
): Promise<RetentionPolicy> {
  return apiPut<RetentionPolicy>("/logging/v1/retention", body);
}

/** `DELETE /api/logging/v1/retention` — отключить ротацию (хранить вечно), 204. */
export function disableRetention(): Promise<void> {
  return apiDelete<void>("/logging/v1/retention");
}
