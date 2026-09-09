/**
 * Тонкие обёртки над `testing_service` `/test-stands/*`, нужные консоли
 * сервера, чтобы обнаружить активный прогон теста на этом стенде (§8.6 плана
 * миграции — кнопка «Живой лог теста»). Источник истины (backend) —
 * `testing_service/src/api/v1/endpoints/test_stands.py`.
 *
 * Каталог тестов/очередь/СТП (`src/pages/testing/*`) сюда не входит — та
 * интеграция отложена отдельным этапом плана.
 */

import { apiGet } from "@/api/client";
import type {
  QueueItemSummary,
  TestingPaginatedResponse,
  TestStandSummary,
} from "@/api/testing/types";

const BASE = "/testing/v1";

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
