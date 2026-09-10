/**
 * Тонкие обёртки над `testing_service` `/department-integration-settings/*`
 * (§2.4, §3.5 плана миграции) — URL'ы Jira/Confluence/Bitbucket отдела +
 * `credential_id`'шники (сам секрет хранится в `secret_service` и по этому
 * API не отдаётся). GET открыт любому аутентифицированному актору и никогда
 * не 404 — отсутствие строки означает «интеграция не настроена» (все поля
 * `null`). PUT (upsert) — под матрицей
 * `(department_integration_settings, *, update)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/department_integration_settings.py`.
 */

import { apiGet, apiPut } from "@/api/client";
import type {
  DepartmentIntegrationSettings,
  DepartmentIntegrationSettingsUpdateRequest,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** `GET /department-integration-settings/{department_id}` — эффективные настройки. */
export function getDepartmentIntegrationSettings(
  departmentId: string,
): Promise<DepartmentIntegrationSettings> {
  return apiGet<DepartmentIntegrationSettings>(
    `${BASE}/department-integration-settings/${departmentId}`,
  );
}

/**
 * `PUT /department-integration-settings/{department_id}` — upsert, все поля
 * опциональны. Доступ: `(department_integration_settings, *, update)`.
 */
export function upsertDepartmentIntegrationSettings(
  departmentId: string,
  body: DepartmentIntegrationSettingsUpdateRequest,
): Promise<DepartmentIntegrationSettings> {
  return apiPut<DepartmentIntegrationSettings>(
    `${BASE}/department-integration-settings/${departmentId}`,
    body,
  );
}
