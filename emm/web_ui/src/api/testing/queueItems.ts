/**
 * Обёртки над публичным API очереди `testing_service` (`/queue-items/*` и
 * управление очередью стенда на `/test-stands/{id}/resume-queue`).
 *
 * Источник истины — `testing_service/src/api/v1/endpoints/queue_items.py`
 * и `test_stands.py`.
 */
import { apiGet, apiPatch, apiPost } from "@/api/client";
import type { ActiveQueueMode, QueueVerdict, TestingPaginatedResponse, VerdictSource } from "./types";
export { getCurrentQueueItem, findActiveQueueItemForServer } from "@/api/testing/testStands";

/**
 * Запрошенное прерывание исполняющегося теста. Проставляется `skip`/`pause`,
 * когда item реально исполняется на стенде — воркер подтвердит прерывание
 * асинхронно, до этого item остаётся в `running`.
 */
export type QueueInterruptAction = "skip" | "pause";

export interface PublicQueueItem {
  log_status?: "available" | "rotated" | "pending" | "missing";
  test_code?: string | null; test_name?: string | null; is_current?: boolean;
  id: string; test_id: string; stand_id: string; test_run_id: string | null;
  retry_of_id: string | null; debug_mode: boolean; prepare_only?: boolean; state: string;
  interrupt_action?: QueueInterruptAction | null;
  rc: string | null; kernel: string | null; mode: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; error: string | null;
  /** Место в очереди стенда (меньше — раньше); значимо между не терминальными элементами. */
  position?: number;
  /**
   * Вердикт: `passed`/`failed`/`unknown`. `unknown` — «результат не
   * определён»: у запуска нет прогона в Zephyr (debug), исход смотреть в
   * логе/Confluence. `null` — ещё не вынесен.
   */
  verdict?: QueueVerdict | null;
  /** Откуда взят исход: статус в Zephyr или код выхода `starter.sh`. */
  verdict_source?: VerdictSource | null;
  /** Статус тест-кейса в Zephyr как есть (последний прочитанный). */
  zephyr_status_raw?: string | null;
  verdict_resolved_at?: string | null;
  /** Многоступенчатый тест: индекс текущего шага с 0 и число шагов. */
  current_step_index?: number;
  step_count?: number | null;
}
export interface QueueLaunchRequest {
  request_id: string; test_id: string; stand_id: string; os_version_id: string;
  kernel: string; debug_mode: boolean;
  /**
   * Легаси testenv-режим: только откатить/подготовить стенд, не запускать
   * тест. Вместо результата воркер оставляет на стенде `command.txt` с
   * командой, которая была бы запущена.
   */
  prepare_only?: boolean;
  /**
   * Забрать стенд у текущего держателя (`busy`/`testing_done`). Без роли
   * department_admin/`admin` testing_service своего отдела сервер отклоняет
   * запрос отдельным `FORCE_LAUNCH_DENIED`; `updating` и чужой `acs` не
   * отбираются никогда (`STAND_TAKEOVER_NOT_ALLOWED`).
   */
  force?: boolean;
  /**
   * Режим при уже активной очереди стенда: `append` — в конец, `replace` —
   * очистить очередь, прервать текущий тест и начать сразу. Не задан — админ
   * получает 409 `STAND_QUEUE_ACTIVE`, остальные встают в конец.
   */
  on_active_queue?: ActiveQueueMode;
}
export interface QueueItemsQuery {
  kind?: "standalone" | "campaign" | "all";
  os_version_id?: string; os_version_name?: string; kernel?: string;
  test_run_id?: string; stand_id?: string; test_id?: string;
  attempt_id?: string; retry_of_id?: string;
  created_from?: string; created_until?: string;
  states?: string[]; debug_mode?: boolean; q?: string;
  order?: "asc" | "desc"; limit?: number; offset?: number;
}
export function listQueueItems(query: QueueItemsQuery = {}) {
  const params = new URLSearchParams({ kind: "standalone", limit: "50" });
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === "") continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item));
    else params.set(key, String(value));
  }
  return apiGet<TestingPaginatedResponse<PublicQueueItem>>(`/testing/v1/queue-items?${params}`);
}
export function launchQueueItem(body: QueueLaunchRequest) {
  return apiPost<PublicQueueItem>("/testing/v1/queue-items", body);
}
export function retryQueueItem(id: string, requestId: string) {
  return apiPost<PublicQueueItem>(`/testing/v1/queue-items/${encodeURIComponent(id)}/retry`, { request_id: requestId });
}

/**
 * `POST /queue-items/{id}/skip` — пропустить тест. Если он уже исполняется на
 * стенде, ответ приходит с прежним `state: "running"` и
 * `interrupt_action: "skip"` — прерывание подтвердит воркер, перечитывать
 * состояние нужно опросом. Из `queued`/`preparing`/`ready` item уходит в
 * `skipped` сразу, стенд продолжает со следующего.
 */
export function skipQueueItem(id: string) {
  return apiPost<PublicQueueItem>(`/testing/v1/queue-items/${encodeURIComponent(id)}/skip`, {});
}

/**
 * `POST /queue-items/{id}/pause` — остановить тест без исхода. Семантика
 * ответа та же, что у `skip`, целевое состояние — `paused`; стенд на
 * следующий item не переходит, пока не вызван `resumeStandQueue`.
 */
export function pauseQueueItem(id: string) {
  return apiPost<PublicQueueItem>(`/testing/v1/queue-items/${encodeURIComponent(id)}/pause`, {});
}

/**
 * `POST /test-stands/{id}/resume-queue` — снять стенд с паузы: единственный
 * `paused`-item переставляется в конец очереди этого стенда и возвращается в
 * `queued`. 409 `STAND_NOT_PAUSED`, если у стенда нет остановленного item'а.
 */
export function resumeStandQueue(standId: string) {
  return apiPost<PublicQueueItem>(`/testing/v1/test-stands/${encodeURIComponent(standId)}/resume-queue`, {});
}

/**
 * `PATCH /test-stands/{id}/queue/order` — новый порядок ещё не начатых
 * (`queued`) элементов стенда (D15). `queueItemIds` — все текущие `queued`
 * стенда в желаемом порядке; активный/остановленный элемент не передаётся и
 * не переставляется (409 `QUEUE_ITEM_NOT_QUEUED`). Если очередь успела
 * измениться (головной стартовал, в хвост встал новый) — 409
 * `QUEUE_ORDER_STALE`, очередь нужно перечитать.
 */
export function reorderStandQueue(standId: string, queueItemIds: string[]) {
  return apiPatch<{ items: PublicQueueItem[] }>(
    `/testing/v1/test-stands/${encodeURIComponent(standId)}/queue/order`,
    { queue_item_ids: queueItemIds },
  );
}
