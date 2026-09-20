/** Общие хелперы строки колокола — для списка и для сортировки в `NotificationBell`. */
import type { AppNotification } from "@/components/notifications/types";

/** Стабильный React-ключ: id задач и элементов очереди из разных сервисов могут совпасть. */
export function rowKey(n: AppNotification): string {
  return n.kind === "worker_task" ? `worker_task:${n.task.id}` : `test_run:${n.item.id}`;
}

/** Время строки: момент завершения, а пока его нет — создания. */
export function rowTime(n: AppNotification): string {
  return n.kind === "worker_task"
    ? (n.task.finished_at ?? n.task.created_at)
    : (n.item.finished_at ?? n.item.created_at);
}
