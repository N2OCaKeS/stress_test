/**
 * Тонкие обёртки над `testing_service`
 * `/departments/{department_id}/activity-reports/*` (§2.7, §9.1 плана
 * миграции) — ручная генерация HR-отчёта по активности + история прошлых
 * генераций. Department-scoped: `department_admin` своего отдела ИЛИ
 * носитель `admin` service-роли `testing_service` в этом же отделе — не
 * открытое чтение, как у платформенных каталогов сервиса.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/department_activity_reports.py`.
 */

import { apiGet, apiPost } from "@/api/client";
import type {
  DepartmentActivityReport,
  DepartmentActivityReportGenerateRequest,
  TestingPaginatedResponse,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/**
 * `POST /departments/{department_id}/activity-reports/generate` — сгенерировать
 * HR-отчёт за период (`'YYYY-MM'`). Частичный провал одного источника
 * (Bitbucket/Jira/Tempo) не рушит отчёт — публикуется с нулями по
 * недоступному источнику, причина видна в `error` ответа.
 */
export function generateDepartmentActivityReport(
  departmentId: string,
  body: DepartmentActivityReportGenerateRequest,
): Promise<DepartmentActivityReport> {
  return apiPost<DepartmentActivityReport>(
    `${BASE}/departments/${departmentId}/activity-reports/generate`,
    body,
  );
}

/** Параметры истории генераций HR-отчёта. */
export interface ListDepartmentActivityReportsQuery {
  limit?: number;
  offset?: number;
}

/** `GET /departments/{department_id}/activity-reports` — история генераций HR-отчёта отдела. */
export function listDepartmentActivityReports(
  departmentId: string,
  query: ListDepartmentActivityReportsQuery = {},
): Promise<TestingPaginatedResponse<DepartmentActivityReport>> {
  return apiGet<TestingPaginatedResponse<DepartmentActivityReport>>(
    `${BASE}/departments/${departmentId}/activity-reports`,
    { query: { ...query } },
  );
}
