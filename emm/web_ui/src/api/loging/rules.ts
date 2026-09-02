/**
 * Thin wrappers для loging_service `/rules` (CRUD правил аудита).
 *
 * Backend под `/api/logging/v1/rules`. Доступ — только `loging_admin`:
 * и чтение (`GET /rules`), и запись закрыты одной dependency `require_admin`.
 * `loging_reader` и `account_admin` получают 403 даже на список.
 *
 * Source of truth: loging_service/src/api/v1/endpoints/rules.py
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  Rule,
  RuleCreateRequest,
  RuleListResponse,
  RuleUpdateRequest,
} from "@/api/loging/types";

/** `GET /api/logging/v1/rules` — список правил, `priority DESC`. */
export function listRules(
  query: { limit?: number; offset?: number } = {},
): Promise<RuleListResponse> {
  return apiGet<RuleListResponse>("/logging/v1/rules", {
    query: { limit: query.limit, offset: query.offset },
  });
}

/**
 * `POST /api/logging/v1/rules`.
 *
 * `effect` обязателен; `effect_severity` обязателен ровно при
 * `effect=OVERRIDE_SEVERITY`. Вызывающий обязан собрать тело корректно —
 * иначе backend вернёт 422.
 */
export function createRule(body: RuleCreateRequest): Promise<Rule> {
  return apiPost<Rule>("/logging/v1/rules", body);
}

/** `PATCH /api/logging/v1/rules/{id}` — partial-update. */
export function updateRule(
  id: string,
  body: RuleUpdateRequest,
): Promise<Rule> {
  return apiPatch<Rule>(`/logging/v1/rules/${id}`, body);
}

/** `DELETE /api/logging/v1/rules/{id}` — 204. */
export function deleteRule(id: string): Promise<void> {
  return apiDelete<void>(`/logging/v1/rules/${id}`);
}
