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
  OffsetPaginatedResponse,
  ReasonBody,
  Server,
  ServerCreateRequest,
  ServerDrift,
  ServerPrepareRequest,
  ServerPrepareResponse,
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

// ── drift ───────────────────────────────────────────────────────────────────

/** `GET /api/server/v1/servers/{id}/drift` — drift-сводка за окно. */
export function getServerDrift(id: string): Promise<ServerDrift> {
  return apiGet<ServerDrift>(`/server/v1/servers/${id}/drift`);
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
