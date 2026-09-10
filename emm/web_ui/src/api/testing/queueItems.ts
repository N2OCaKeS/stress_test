/**
 * Публичный API `testing_service` не заводит отдельный список/детали
 * `queue_items` верхнего уровня — оба internal-эндпоинта очереди
 * (`POST /internal/queue/claim`, `POST /internal/queue/{id}/completed`,
 * см. `testing_service/src/api/v1/endpoints/internal_queue.py`) существуют
 * только для `testing_worker` под `SERVICE_API_KEY` и не смонтированы под
 * `/api/testing/v1` — строить на них веб-клиент нельзя и не нужно.
 *
 * Единственная публичная точка входа к состоянию queue_item — уже
 * реализованная `getCurrentQueueItem` в `@/api/testing/testStands`
 * (`GET /test-stands/{id}/current-queue-item`) плюс дочерние queue_items
 * внутри карточки кампании (`TestRunDetail.queue_items`, см.
 * `@/api/testing/testRuns::getTestRun`). Этот файл существует, чтобы явно
 * зафиксировать факт отсутствия отдельного клиента, а не молчаливо пропустить
 * пункт плана.
 */

export { getCurrentQueueItem, findActiveQueueItemForServer } from "@/api/testing/testStands";
