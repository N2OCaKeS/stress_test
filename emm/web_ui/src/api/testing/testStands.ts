/**
 * Тонкие обёртки над `testing_service` `/test-stands/*` (§2.3, §4 плана
 * миграции). Изначально файл нёс только то, что нужно консоли сервера, чтобы
 * обнаружить активный прогон теста на этом стенде (§8.6 — кнопка «Живой лог
 * теста»); с волны 11 расширен полным CRUD стендов. Источник истины (backend)
 * — `testing_service/src/api/v1/endpoints/test_stands.py`.
 *
 * Чтение (список/карточка) доступно любому аутентифицированному актору,
 * запись — под матрицей `(test_stand, *, create|update|delete)`.
 */

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import type {
  QueueItemSummary,
  TestingOkResponse,
  TestingPaginatedResponse,
  TestStand,
  TestStandCreateRequest,
  TestStandMetricsResponse,
  TestStandSummary,
  TestStandTestCredentials,
  TestStandUpdateRequest,
} from "@/api/testing/types";

const BASE = "/testing/v1";

/** Параметры списка стендов — пагинация + фильтры. */
export interface ListTestStandsQuery {
  limit?: number;
  offset?: number;
  department_id?: string;
  is_active?: boolean;
  queue_enabled?: boolean;
  server_id?: string;
}

/** `GET /test-stands` — страница списка стендов. Любой аутентифицированный актор. */
export function listTestStands(
  query: ListTestStandsQuery = {},
): Promise<TestingPaginatedResponse<TestStand>> {
  return apiGet<TestingPaginatedResponse<TestStand>>(`${BASE}/test-stands`, {
    query: { ...query },
  });
}

/**
 * `GET /test-stands/{id}` — карточка стенда, обогащённая живой карточкой
 * сервера из server_service (`server`/`server_unavailable`).
 */
export function getTestStand(standId: string): Promise<TestStand> {
  return apiGet<TestStand>(`${BASE}/test-stands/${standId}`);
}

/**
 * `POST /test-stands` — зарегистрировать сервер/ВМ из server_service как
 * тестовый стенд. `department_id` резолвится сервером, клиент его не задаёт.
 * Доступ: `(test_stand, *, create)`.
 */
export function createTestStand(
  body: TestStandCreateRequest,
): Promise<TestStand> {
  return apiPost<TestStand>(`${BASE}/test-stands`, body);
}

/**
 * `PATCH /test-stands/{id}` — изменяемы только `queue_enabled`/`is_active`.
 * Доступ: `(test_stand, *, update)`.
 */
export function updateTestStand(
  standId: string,
  body: TestStandUpdateRequest,
): Promise<TestStand> {
  return apiPatch<TestStand>(`${BASE}/test-stands/${standId}`, body);
}

/** `DELETE /test-stands/{id}` — hard-delete записи стенда. Доступ: `(test_stand, *, delete)`. */
export function deleteTestStand(standId: string): Promise<TestingOkResponse> {
  return apiDelete<TestingOkResponse>(`${BASE}/test-stands/${standId}`);
}

/**
 * `GET /test-stands/{id}/test-credentials` — прокси на
 * `GET /servers/{id}/test-credentials` server_service'а (§5.3 плана
 * миграции). Без `reveal` — только метаданные; `reveal: true` добавляет
 * `password_b64`/`ssh_private_key_b64` (CRITICAL-аудит на стороне
 * server_service). Доступ: `(test_stand, *, view_test_credentials)`.
 */
export function getTestStandCredentials(
  standId: string,
  reveal = false,
): Promise<TestStandTestCredentials> {
  return apiGet<TestStandTestCredentials>(
    `${BASE}/test-stands/${standId}/test-credentials`,
    { query: { reveal } },
  );
}

/**
 * Стенд, привязанный к этому Server/Vm.id, если он заведён — `server_id`
 * уникален на стороне backend, поэтому результат либо один элемент, либо
 * пусто (сервер вообще не заведён как тестовый стенд).
 */
export async function findStandByServerId(
  serverId: string,
): Promise<TestStandSummary | null> {
  const resp = await apiGet<TestingPaginatedResponse<TestStandSummary>>(
    `${BASE}/test-stands`,
    { query: { server_id: serverId, limit: 1 } },
  );
  return resp.items[0] ?? null;
}

/**
 * `GET /test-stands/metrics` — живые CPU/RAM активных стендов отдела, прямым
 * скрейпом node_exporter'а (`testing_service/src/services/stand_metrics.py`).
 * Недоступный стенд/exporter отдаёт 0, не ошибку — карточка не гаснет из-за
 * одного нерабочего стенда в пуле.
 */
export function listTestStandMetrics(): Promise<TestStandMetricsResponse> {
  return apiGet<TestStandMetricsResponse>(`${BASE}/test-stands/metrics`);
}

/** Активный элемент очереди стенда, если есть (`null` — очередь сейчас пуста). */
export function getCurrentQueueItem(
  standId: string,
): Promise<QueueItemSummary | null> {
  return apiGet<QueueItemSummary | null>(
    `${BASE}/test-stands/${standId}/current-queue-item`,
  );
}

/**
 * Составной lookup для консоли сервера: по `server_id` карточки сервера
 * найти стенд, а по стенду — активный item очереди. `null` на любом шаге
 * (сервер не стенд / стенд без активной работы) — тоже `null` здесь, кнопка
 * живого лога просто не показывается.
 */
export async function findActiveQueueItemForServer(
  serverId: string,
): Promise<QueueItemSummary | null> {
  const stand = await findStandByServerId(serverId);
  if (!stand) return null;
  return getCurrentQueueItem(stand.id);
}
