/**
 * Типы request/response для `testing_service` API, используемые в этой части
 * фронтенда (§8.6 плана миграции — только то, что нужно кнопке «Живой лог
 * теста» в консоли сервера). Каталог тестов/прогонов/СТП (`src/pages/testing/*`)
 * несёт собственные типы отдельно — они сюда не сводятся.
 */

/** Offset-envelope list-эндпоинтов testing_service (`{items,total,limit,offset}`). */
export interface TestingPaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Минимум карточки стенда, нужный, чтобы по `server_id` найти `stand_id`
 * (`GET /test-stands?server_id=`).
 */
export interface TestStandSummary {
  id: string;
  server_id: string;
  department_id: string;
  queue_enabled: boolean;
  is_active: boolean;
}

/**
 * Ответ `GET /test-stands/{id}/current-queue-item` — активный (не терминальный)
 * элемент очереди стенда, если он сейчас есть.
 */
export interface QueueItemSummary {
  queue_item_id: string;
  state: string;
  test_id: string;
  started_at: string | null;
}
