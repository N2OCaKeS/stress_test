/**
 * Thin wrappers for server_service IPMI endpoints (`/servers/{id}/ipmi/*` +
 * `/ipmi-controllers/{id}/rotate` + worker-dispatch `power.status`).
 *
 * Связь servers ↔ ipmi_controllers — 1:1 (UNIQUE на server_id), поэтому в
 * URL CRUD-эндпоинтов достаточно `{server_id}`; backend сам резолвит
 * controller. Под капотом CRUD идёт через сервисный слой, power-операции
 * (on/off/reboot/status) — через worker_client.dispatch_task, поэтому такие
 * вызовы возвращают `TaskDispatchResponse` (202 + task_id).
 *
 * Источник истины — `server_service/src/api/v1/endpoints/ipmi.py` +
 * `worker_dispatch.py`. Pydantic-схемы — `schemas/ipmi_controller.py`.
 *
 * Endpoint `POST /servers/{id}/ipmi/credentials/rotate` снят 410 GONE
 * (писал ciphertext без BMC apply/verify, любой держатель grant'а мог
 * разорвать out-of-band доступ). Wrapper'а под него нет — каноничный путь
 * ротации — `rotateIpmi(controllerId)` через worker dispatch.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  IpmiController,
  IpmiCreateRequest,
  IpmiCredentials,
  IpmiUpdateRequest,
  PowerStatus,
  TaskDispatchResponse,
} from "@/api/server/types";

// ---------------------------------------------------------------------------
// CRUD: /api/server/v1/servers/{server_id}/ipmi
// ---------------------------------------------------------------------------

/**
 * Зарегистрировать IPMI-контроллер для сервера (1:1).
 *
 * Backend: `POST /api/server/v1/servers/{server_id}/ipmi` (201).
 * Доступ: `(ipmi_controller, *, create)`. Повторная регистрация для того же
 * сервера → 409 IPMI_DUPLICATE (UNIQUE на server_id).
 */
export function registerIpmi(
  serverId: string,
  body: IpmiCreateRequest,
): Promise<IpmiController> {
  return apiPost<IpmiController>(
    `/server/v1/servers/${serverId}/ipmi`,
    body,
  );
}

/**
 * Карточка IPMI-контроллера (с паролем при `view_credentials`).
 *
 * Backend: `GET /api/server/v1/servers/{server_id}/ipmi`.
 * Доступ: `(ipmi_controller, *, view)`; держателю `view_credentials` тот же
 * GET доносит `password_b64 = base64(plaintext)`. Per-IP+server rate-limit
 * `PASSWORD_REVEAL_RATE_LIMIT` поверх глобального — 429 при превышении.
 * Раскрытие пароля пишет CRITICAL audit `ipmi_controller.credentials_revealed`.
 */
export function getIpmi(serverId: string): Promise<IpmiController> {
  return apiGet<IpmiController>(`/server/v1/servers/${serverId}/ipmi`);
}

/**
 * Частичное обновление IPMI-контроллера.
 *
 * Backend: `PATCH /api/server/v1/servers/{server_id}/ipmi`.
 * Доступ: `(ipmi_controller, *, update)`. `password` через PATCH не меняется —
 * для ротации см. `rotateIpmi` (worker dispatch с BMC apply/verify).
 */
export function updateIpmi(
  serverId: string,
  body: IpmiUpdateRequest,
): Promise<IpmiController> {
  return apiPatch<IpmiController>(
    `/server/v1/servers/${serverId}/ipmi`,
    body,
  );
}

/**
 * Hard-delete IPMI-контроллера (CRITICAL аудит).
 *
 * Backend: `DELETE /api/server/v1/servers/{server_id}/ipmi`.
 * Доступ: `(ipmi_controller, *, delete)`. После удаления power-операции на
 * сервере будут отбиваться 404 NO_IPMI_CONTROLLER до повторной регистрации.
 */
export function deleteIpmi(serverId: string): Promise<void> {
  return apiDelete<void>(`/server/v1/servers/${serverId}/ipmi`);
}

// ---------------------------------------------------------------------------
// Power operations: queue worker tasks (202 + task_id)
// ---------------------------------------------------------------------------

/**
 * Поставить задачу `power.on` в очередь worker'а (202).
 *
 * Backend: `POST /api/server/v1/servers/{server_id}/ipmi/power/on`.
 * Доступ: `(server, *, power_on)`. Pre-dispatch валидации: SERVER не
 * DECOMMISSIONED, есть запись в `ipmi_controllers`. Возвращается task_id
 * для отслеживания через `/tasks/{id}`.
 */
export function powerOn(serverId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/ipmi/power/on`,
    {},
  );
}

/**
 * Поставить задачу `power.off` (hard) в очередь worker'а (202).
 *
 * Backend: `POST /api/server/v1/servers/{server_id}/ipmi/power/off`.
 * Доступ: `(server, *, power_off)`. Worker всегда отправляет `ForceOff`
 * (Redfish) / `chassis power off` (ipmitool) — soft/ACPI shutdown через этот
 * endpoint не поддерживается.
 */
export function powerOff(serverId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/ipmi/power/off`,
    {},
  );
}

/**
 * Поставить задачу `power.reboot` в очередь worker'а (202).
 *
 * Backend: `POST /api/server/v1/servers/{server_id}/ipmi/power/reboot`.
 * Доступ: `(server, *, power_reboot)`. Power-cycle через BMC (обычно reset
 * через iDRAC/Redfish, не soft-reboot).
 */
export function powerReboot(serverId: string): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/ipmi/power/reboot`,
    {},
  );
}

/**
 * Кэшированное состояние питания (без live BMC-probe).
 *
 * Backend: `GET /api/server/v1/servers/{server_id}/ipmi/power`.
 * Доступ: `(server, *, view)`. TTL/инвалидации у `power_state` нет:
 * значение перетирается worker'ом при очередном power-callback'е, между
 * обновлениями может быть устаревшим. Live-опрос — `dispatchPowerStatus`.
 */
export function getPowerStatus(serverId: string): Promise<PowerStatus> {
  return apiGet<PowerStatus>(`/server/v1/servers/${serverId}/ipmi/power`);
}

/**
 * Метаданные IPMI-credentials (БЕЗ plaintext-пароля).
 *
 * Backend: `GET /api/server/v1/servers/{server_id}/ipmi/credentials`.
 * Доступ: `(ipmi_controller, *, view_credentials)`. Возвращает kind /
 * endpoint_url / username / `password_rotated_at`; plaintext через этот путь
 * не отдаётся ни при каких условиях — он есть только во внутреннем endpoint'е
 * под worker'ом. Аудит — INFO `ipmi_controller.view_credentials_meta`.
 */
export function getIpmiCredentials(
  serverId: string,
): Promise<IpmiCredentials> {
  return apiGet<IpmiCredentials>(
    `/server/v1/servers/${serverId}/ipmi/credentials`,
  );
}

// ---------------------------------------------------------------------------
// Worker-driven credential rotation: /ipmi-controllers/{id}/rotate
// ---------------------------------------------------------------------------

/**
 * Запустить ротацию IPMI-пароля через worker (Redfish apply + storage).
 *
 * Backend: `POST /api/server/v1/ipmi-controllers/{id}/rotate` (202).
 * Доступ: `(ipmi_controller, *, rotate_credentials)`. Cross-dept controller
 * скрыт за 404 NO_IPMI_CONTROLLER.
 *
 * Worker генерит новый секрет, применяет на BMC, затем ходит во внутренний
 * callback `credentials_rotated`, который проверяет `verified_at` и только
 * после этого шифрует/сохраняет ciphertext (verify-then-store).
 *
 * Внимание: worker-handler сейчас raise'ит NotImplementedError ДО вызова в
 * iDRAC — storage round-trip ещё не построен. Endpoint всё равно поднимает
 * task'у, worker корректно mark_failed + audit failure.
 *
 * Старый user-facing путь `POST /servers/{id}/ipmi/credentials/rotate` снят
 * 410 GONE (писал ciphertext без BMC apply/verify) — wrapper'а под него нет.
 */
export function rotateIpmi(
  controllerId: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/ipmi-controllers/${controllerId}/rotate`,
    {},
  );
}

/**
 * Запросить live состояние питания через BMC (202, worker, ack-only).
 *
 * Backend: `POST /api/server/v1/servers/{server_id}/power/status`.
 * Доступ: `(server, *, power_status)`. Публикует задачу `power.status` в
 * taskiq-broker; worker через Redfish или ipmitool опрашивает PowerState
 * BMC и кладёт результат в `task.result`. Pre-dispatch валидации те же,
 * что у power.on/off/reboot (visibility / role / decommissioned / no_ipmi).
 *
 * Wrapper возвращает только ack `{task_id, status}` — результат читается
 * отдельным запросом через `/tasks/{id}`.
 */
export function dispatchPowerStatus(
  serverId: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/power/status`,
    {},
  );
}
