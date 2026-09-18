/**
 * Общий тип строки колокола уведомлений — объединяет два независимых
 * источника (`useMyTaskNotifications` и `useDepartmentRunNotifications`) под
 * одним `NotificationCenter`, чтобы не заводить второй колокол в TopBar.
 */
import type { TaskNotification } from "@/api/server/useMyTaskNotifications";
import type { TestRunNotification } from "@/api/testing/useDepartmentRunNotifications";

export type AppNotification =
  | ({ kind: "worker_task" } & TaskNotification)
  | ({ kind: "test_run" } & TestRunNotification);
