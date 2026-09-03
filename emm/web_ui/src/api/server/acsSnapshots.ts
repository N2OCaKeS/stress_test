/**
 * Thin wrappers для `server_service` endpoints `/servers/{id}/acs-snapshots...` —
 * полные снимки диска сервера через ACS (Clonezilla-обёртка), не путать с
 * VM-снимками (`@/api/server/vms`).
 *
 * `listAcsSnapshots` — единственный read-эндпоинт раздела; create/restore
 * (single и batch) — dispatch worker-задачи, ответ 202 с `task_id`/`status`
 * (single) либо per-server сводкой (batch).
 *
 * Source of truth:
 *   server_service/src/api/v1/endpoints/worker_dispatch.py (`/acs-snapshots*`)
 *   server_service/src/schemas/server.py (`ServerAcsSnapshot*Request`)
 */

import { apiGet, apiPost } from "@/api/client";
import type {
  AcsAvailabilityResponse,
  AcsSnapshotBatchRequest,
  AcsSnapshotBatchResponse,
  AcsSnapshotListResponse,
  TaskDispatchResponse,
} from "@/api/server/types";

/** `GET /api/server/v1/servers/{id}/acs-snapshots` — снимки этого сервера в ACS. */
export function listAcsSnapshots(serverId: string): Promise<AcsSnapshotListResponse> {
  return apiGet<AcsSnapshotListResponse>(
    `/server/v1/servers/${serverId}/acs-snapshots`,
  );
}

/**
 * `GET /api/server/v1/servers/{id}/acs-availability` — можно ли показывать
 * вкладку «Снимки ACS» для этого сервера. Всегда 200 (`{available: bool}`),
 * кроме 404 если сервер не виден — используется, чтобы решить видимость
 * вкладки ДО попытки реального листинга снимков.
 */
export function getAcsAvailability(serverId: string): Promise<AcsAvailabilityResponse> {
  return apiGet<AcsAvailabilityResponse>(
    `/server/v1/servers/${serverId}/acs-availability`,
  );
}

/**
 * `POST /api/server/v1/servers/{id}/acs-snapshots` — создать снимок диска.
 *
 * Ставит `busy_state=acs` до callback'а `record_acs_snapshot_created`.
 * Доступ — `(server, *, acs_snapshot_create)`, инстанс-грант допустим.
 */
export function createAcsSnapshot(
  serverId: string,
  osVersionId: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/acs-snapshots`,
    { os_version_id: osVersionId },
  );
}

/**
 * `POST /api/server/v1/servers/{id}/acs-snapshots/restore` — восстановить
 * сервер из снимка (полная перезапись диска, необратимо).
 *
 * Право `acs_snapshot_restore` — только тип-wide admin, инстанс-грант
 * невозможен (`Action.ACS_SNAPSHOT_RESTORE` из `_NON_INSTANCE_ACTIONS`).
 */
export function restoreAcsSnapshot(
  serverId: string,
  osVersionId: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${serverId}/acs-snapshots/restore`,
    { os_version_id: osVersionId },
  );
}

/**
 * `POST /api/server/v1/servers/acs-snapshots/create-batch` — массовое
 * создание снимков на нескольких серверах под одну версию каталога.
 */
export function createAcsSnapshotsBatch(
  serverIds: string[],
  osVersionId: string,
): Promise<AcsSnapshotBatchResponse> {
  const body: AcsSnapshotBatchRequest = {
    server_ids: serverIds,
    os_version_id: osVersionId,
  };
  return apiPost<AcsSnapshotBatchResponse>(
    "/server/v1/servers/acs-snapshots/create-batch",
    body,
  );
}

/**
 * `POST /api/server/v1/servers/acs-snapshots/restore-batch` — массовое
 * восстановление нескольких серверов из снимков версии каталога. Только
 * тип-wide admin (см. `restoreAcsSnapshot`).
 */
export function restoreAcsSnapshotsBatch(
  serverIds: string[],
  osVersionId: string,
): Promise<AcsSnapshotBatchResponse> {
  const body: AcsSnapshotBatchRequest = {
    server_ids: serverIds,
    os_version_id: osVersionId,
  };
  return apiPost<AcsSnapshotBatchResponse>(
    "/server/v1/servers/acs-snapshots/restore-batch",
    body,
  );
}
