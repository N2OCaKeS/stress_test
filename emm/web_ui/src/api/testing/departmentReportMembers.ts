/**
 * Тонкие обёртки над `testing_service`
 * `/departments/{department_id}/report-members/*` (§9.1 плана миграции) —
 * список сотрудников отдела, учитываемых HR-отчётом по активности. Чтение
 * открыто любому аутентифицированному актору, запись — под матрицей
 * `(department_report_member, *, create|update|delete)`.
 *
 * Source of truth: `testing_service/src/api/v1/endpoints/department_report_members.py`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  DepartmentReportMember,
  DepartmentReportMemberCreateRequest,
  DepartmentReportMemberUpdateRequest,
  TestingOkResponse,
  TestingPaginatedResponse,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** Параметры списка сотрудников отдела. */
export interface ListDepartmentReportMembersQuery {
  limit?: number;
  offset?: number;
  is_active?: boolean;
}

/** `GET /departments/{department_id}/report-members` — список сотрудников отдела для HR-отчёта. */
export function listDepartmentReportMembers(
  departmentId: string,
  query: ListDepartmentReportMembersQuery = {},
): Promise<TestingPaginatedResponse<DepartmentReportMember>> {
  return apiGet<TestingPaginatedResponse<DepartmentReportMember>>(
    `${BASE}/departments/${departmentId}/report-members`,
    { query: { ...query } },
  );
}

/**
 * `POST /departments/{department_id}/report-members` — добавить сотрудника.
 * Доступ: `(department_report_member, *, create)`.
 */
export function createDepartmentReportMember(
  departmentId: string,
  body: DepartmentReportMemberCreateRequest,
): Promise<DepartmentReportMember> {
  return apiPost<DepartmentReportMember>(
    `${BASE}/departments/${departmentId}/report-members`,
    body,
  );
}

/** `GET /departments/{department_id}/report-members/{member_id}` — карточка сотрудника. */
export function getDepartmentReportMember(
  departmentId: string,
  memberId: string,
): Promise<DepartmentReportMember> {
  return apiGet<DepartmentReportMember>(
    `${BASE}/departments/${departmentId}/report-members/${memberId}`,
  );
}

/**
 * `PATCH /departments/{department_id}/report-members/{member_id}` —
 * частичное обновление. Доступ: `(department_report_member, *, update)`.
 */
export function updateDepartmentReportMember(
  departmentId: string,
  memberId: string,
  body: DepartmentReportMemberUpdateRequest,
): Promise<DepartmentReportMember> {
  return apiPatch<DepartmentReportMember>(
    `${BASE}/departments/${departmentId}/report-members/${memberId}`,
    body,
  );
}

/**
 * `DELETE /departments/{department_id}/report-members/{member_id}` —
 * удалить сотрудника из HR-отчёта. Доступ: `(department_report_member, *,
 * delete)`.
 */
export function deleteDepartmentReportMember(
  departmentId: string,
  memberId: string,
): Promise<TestingOkResponse> {
  return apiDelete<TestingOkResponse>(
    `${BASE}/departments/${departmentId}/report-members/${memberId}`,
  );
}
