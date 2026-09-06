/**
 * Настройки SSH-доступа к хосту ALLTA своего отдела (systemd-юниты,
 * управляемые со страницы «Здоровье служб») плюс CRUD списка юнитов отдела.
 *
 * Per-department: host/port/user + приватный ключ + произвольный список
 * unit_name/label — каждый отдел настраивает свой хост и свой список юнитов.
 * `department_id` нигде не передаётся параметром — endpoint всегда работает
 * с отделом caller'а, резолвится backend'ом из identity. Ключ write-only —
 * GET отдаёт только факт `private_key_is_set`, само значение никогда не
 * возвращается (тот же паттерн, что у пароля ACS в `acsSettings.ts`).
 *
 * Источник истины — `server_service`:
 *   GET/PUT /api/server/v1/settings/host-services
 *   GET/POST /api/server/v1/settings/host-services/units
 *   PATCH/DELETE /api/server/v1/settings/host-services/units/{unit_id}
 * Гейтится department_admin или носителем роли `server_service.admin` своего
 * отдела (`canManageHostServices`); остальным (включая account_admin — у него
 * нет department_id) backend ответит 403.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "@/api/client";
import type {
  HostServicesSettings,
  HostServicesSettingsUpdate,
  HostServiceUnit,
  HostServiceUnitCreate,
  HostServiceUnitListResponse,
  HostServiceUnitUpdate,
} from "@/api/server/types";

const BASE = "/server/v1";

/** Прочитать текущие настройки SSH-доступа к хосту своего отдела. */
export function getHostServicesSettings(): Promise<HostServicesSettings> {
  return apiGet<HostServicesSettings>(`${BASE}/settings/host-services`);
}

/** Обновить настройки SSH-доступа своего отдела (частичное обновление). */
export function updateHostServicesSettings(
  body: HostServicesSettingsUpdate,
): Promise<HostServicesSettings> {
  return apiPut<HostServicesSettings>(`${BASE}/settings/host-services`, body);
}

/** Список systemd-юнитов, заведённых своим отделом. */
export function listHostServiceUnits(): Promise<HostServiceUnitListResponse> {
  return apiGet<HostServiceUnitListResponse>(`${BASE}/settings/host-services/units`);
}

/** Завести новый юнит. 409 — unit_name уже есть в отделе. */
export function createHostServiceUnit(
  body: HostServiceUnitCreate,
): Promise<HostServiceUnit> {
  return apiPost<HostServiceUnit>(`${BASE}/settings/host-services/units`, body);
}

/** Переименовать label юнита (unit_name сам не редактируется). */
export function renameHostServiceUnit(
  unitId: string,
  body: HostServiceUnitUpdate,
): Promise<HostServiceUnit> {
  return apiPatch<HostServiceUnit>(
    `${BASE}/settings/host-services/units/${unitId}`,
    body,
  );
}

/** Удалить юнит из списка отдела. */
export function deleteHostServiceUnit(unitId: string): Promise<void> {
  return apiDelete<void>(`${BASE}/settings/host-services/units/${unitId}`);
}
