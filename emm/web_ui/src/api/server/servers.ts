/**
 * Thin wrappers для `server_service` `/servers/*` endpoints.
 *
 * Один метод на backend-route, типы — из `./types.ts`. Список серверов
 * возвращает offset-envelope (`{items,total,limit,offset}`); для cursor
 * семантики добавь `cursor: true` / `after` в `query` через сырой
 * `request()` — здесь поддержан только базовый offset-режим (как в спеке).
 *
 * Source of truth:
 *   server_service/src/api/v1/endpoints/servers.py
 *   server_service/src/api/v1/endpoints/inventory.py
 *   server_service/src/api/v1/endpoints/worker_dispatch.py
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  BulkPrepareRequest,
  BulkPrepareResponse,
  OffsetPaginatedResponse,
  ReasonBody,
  Server,
  ServerCleanRequest,
  ServerCleanResponse,
  ServerCreateRequest,
  ServerPrepareBatchRequest,
  ServerPrepareBatchResponse,
  ServerPrepareRequest,
  ServerPrepareResponse,
  ServerTestCredentials,
  ServerUpdateRequest,
  TaskDispatchResponse,
} from "@/api/server/types";

// ── list / detail / mutation ────────────────────────────────────────────────

/**
 * Параметры фильтрации списка серверов.
 *
 * `department_id`/`status`/`busy` сейчас не объявлены backend'ом, но
 * URLSearchParams их прокинет — UI готов к расширению endpoint'а.
 */
export interface ListServersQuery {
  department_id?: string;
  status?: string;
  busy?: string;
  limit?: number;
  offset?: number;
}

/** `GET /api/server/v1/servers` — страница серверов (offset envelope). */
export function listServers(
  query: ListServersQuery = {},
): Promise<OffsetPaginatedResponse<Server>> {
  const params: Record<string, string | number | boolean | null | undefined> = {
    department_id: query.department_id,
    status: query.status,
    busy: query.busy,
    limit: query.limit,
    offset: query.offset,
  };
  return apiGet<OffsetPaginatedResponse<Server>>("/server/v1/servers", {
    query: params,
  });
}

/** `GET /api/server/v1/servers/{id}` — карточка сервера. */
export function getServer(id: string): Promise<Server> {
  return apiGet<Server>(`/server/v1/servers/${id}`);
}

/** `POST /api/server/v1/servers` — регистрация нового сервера. */
export function createServer(body: ServerCreateRequest): Promise<Server> {
  return apiPost<Server>("/server/v1/servers", body);
}

/** `PATCH /api/server/v1/servers/{id}` — частичное обновление карточки. */
export function updateServer(
  id: string,
  body: ServerUpdateRequest,
): Promise<Server> {
  return apiPatch<Server>(`/server/v1/servers/${id}`, body);
}

/**
 * `DELETE /api/server/v1/servers/{id}` — жёсткое удаление с каскадом.
 *
 * `body.reason` идёт в audit-payload, backend сейчас тело не парсит, но
 * UI всегда сопровождает delete причиной, чтобы её было видно в журнале.
 */
export function deleteServer(id: string, body: ReasonBody): Promise<void> {
  return apiDelete<void>(`/server/v1/servers/${id}`, body);
}

// ── busy-lease ──────────────────────────────────────────────────────────────

/**
 * `POST /api/server/v1/servers/{id}/busy` — захват сервера.
 *
 * UI передаёт человеческую причину захвата; backend кладёт её в `busy_note`
 * через `ServerAcquireRequest.purpose`.
 */
export function setBusy(id: string, body: ReasonBody): Promise<Server> {
  return apiPost<Server>(`/server/v1/servers/${id}/busy`, {
    purpose: body.reason,
  });
}

/** `DELETE /api/server/v1/servers/{id}/busy` — освободить сервер. */
export function clearBusy(id: string): Promise<Server> {
  return apiDelete<Server>(`/server/v1/servers/${id}/busy`);
}

// ── inventory / prepare dispatch'и (202 task_id) ───────────────────────────

/**
 * `POST /api/server/v1/servers/{id}/inventory/sync` — запустить inventory
 * SSH-задачу через worker. Ответ — `task_id` для последующего трекинга.
 *
 * Worker заходит по SSH под управляющим пользователем (`management_user`) —
 * сервер обязан быть подготовлен (`is_managed`, через prepare), иначе backend
 * вернёт 409 `PREPARE_REQUIRED`. Поэтому `account_id` больше не нужен и в
 * запрос не уходит (параметр оставлен для обратной совместимости сигнатуры).
 */
export function inventorySync(
  id: string,
  _opts: { account_id?: string } = {},
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${id}/inventory/sync`,
  );
}

/**
 * `POST /api/server/v1/servers/{id}/prepare` — бутстрап управления.
 *
 * Тело опционально на уровне сигнатуры, но backend требует
 * `{username_b64, password_b64}` (base64-encoded bootstrap creds). Если
 * UI вызовет без body — придёт 422; правильное использование — заполнить
 * `ServerPrepareRequest`.
 */
export function prepareServer(
  id: string,
  body?: ServerPrepareRequest,
): Promise<ServerPrepareResponse> {
  return apiPost<ServerPrepareResponse>(
    `/server/v1/servers/${id}/prepare`,
    body,
  );
}

/**
 * `POST /api/server/v1/servers/{id}/install-node-exporter` — поставить
 * node_exporter на managed-сервер через worker. Результат установки лежит в
 * `GET /tasks/{task_id}`.
 */
export function installNodeExporter(
  id: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${id}/install-node-exporter`,
  );
}

/**
 * `POST /api/server/v1/servers/{id}/astra-update` — обновить ОС Astra до
 * версии каталога.
 *
 * Backend перезаписывает `/etc/apt/sources.list` репозиториями выбранной
 * `OsVersion` и гонит `apt update && astra-update`. На время обновления
 * сервер помечается `busy_state='updating'`, любые другие операции над ним
 * отбиваются 409 `SERVER_UPDATING` до завершения. Ответ — `task_id` задачи.
 */
export function astraUpdate(
  id: string,
  body: { os_version_id: string },
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${id}/astra-update`,
    body,
  );
}

/**
 * `POST /api/server/v1/servers/prepare/bulk` — массовый prepare.
 *
 * Принимает per-server bootstrap-креды (`username_b64` / `password_b64` в
 * base64, опц. `ssh_private_key_b64`). Возвращает per-server исходы:
 * `queued` (задача поставлена) либо `skipped` (с `reason`).
 */
export function prepareServersBulk(
  body: BulkPrepareRequest,
): Promise<BulkPrepareResponse> {
  return apiPost<BulkPrepareResponse>("/server/v1/servers/prepare/bulk", body);
}

/**
 * `POST /api/server/v1/servers/prepare-batch` — массовый prepare с per-server
 * выбором режима bootstrap-кред.
 *
 * На каждый сервер ровно один режим: привязанная учётка (`account_id`) либо
 * ручной ввод (`username_b64`+`password_b64`, опц. `ssh_private_key_b64`) —
 * те же поля, что у single-prepare. Возвращает `batch_id` + per-server
 * разбивку: `dispatched` (задача поставлена, `task_id`) и `failed` (с `reason`).
 */
export function prepareServersBatch(
  body: ServerPrepareBatchRequest,
): Promise<ServerPrepareBatchResponse> {
  return apiPost<ServerPrepareBatchResponse>(
    "/server/v1/servers/prepare-batch",
    body,
  );
}

/**
 * `POST /api/server/v1/servers/{id}/clean` — оркестрация очистки после
 * переустановки ОС.
 *
 * Четыре независимых флага (`unbind_accounts` / `update_os_version` /
 * `rerun_prepare` / `run_inventory_sync`) выполняются в фиксированном порядке.
 * `os_version_id` учитывается при `update_os_version`, блок `prepare`
 * обязателен при `rerun_prepare`. Ответ — per-action сводка с исходами
 * `done | dispatched | skipped | failed`.
 */
export function cleanServer(
  id: string,
  body: ServerCleanRequest,
): Promise<ServerCleanResponse> {
  return apiPost<ServerCleanResponse>(`/server/v1/servers/${id}/clean`, body);
}

/**
 * `POST /api/server/v1/servers/{id}/management-credentials/rotate` — ротация
 * управляющей пары/пароля сервера (фича #3).
 *
 * Гейт — `(server, *, update)`, тот же, что у prepare; сервер обязан быть
 * prepared (`is_managed`), иначе backend вернёт 409 PREPARE_REQUIRED.
 * Диспатчит worker-задачу `server.rotate_management_creds` и отвечает 202 с
 * `{task_id, status}`. Аудит — CRITICAL. Сразу после диспатча у сервера
 * выставляется `mgmt_creds_pending_apply=True`, пока worker не подтвердит.
 */
export function rotateManagementCredentials(
  id: string,
): Promise<TaskDispatchResponse> {
  return apiPost<TaskDispatchResponse>(
    `/server/v1/servers/${id}/management-credentials/rotate`,
  );
}

/**
 * `GET /api/server/v1/servers/{id}/test-credentials` — учётка исполнения
 * теста стенда (план ALLTA MIGRATION §5.3).
 *
 * Без `reveal` отдаёт только метаданные (`password_b64`/`ssh_private_key_b64`
 * всегда `null`). С `reveal: true` backend доотдаёт plaintext в base64 —
 * держателю `view_test_credentials` (по умолчанию только роль `admin`) — и
 * фиксирует отдельное CRITICAL-audit действие; частые повторы отбиваются 429.
 */
export function getServerTestCredentials(
  serverId: string,
  opts?: { reveal?: boolean },
): Promise<ServerTestCredentials> {
  return apiGet<ServerTestCredentials>(
    `/server/v1/servers/${serverId}/test-credentials`,
    { query: opts?.reveal ? { reveal: true } : undefined },
  );
}
