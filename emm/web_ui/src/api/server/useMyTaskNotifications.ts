/**
 * Поллинг «моих» worker-task'ей для колокола уведомлений в TopBar.
 *
 * Опрашивает `GET /tasks?created_by=<me>` раз в ~10 секунд — колокол показывает
 * строго мои задачи при любой роли (фильтр пересекается с role-scope на backend,
 * видимость не расширяет). Все задачи отдела админ смотрит отдельно — в панели
 * на главной.
 *
 * Когда задача впервые приходит в терминальном статусе (succeeded/failed/
 * cancelled), которого мы по ней ещё не видели, показываем toast и помечаем
 * запись непрочитанной. Прочитанность и последний виденный статус храним в
 * localStorage под ключом конкретного пользователя — переживает перезагрузку и
 * не течёт между разными залогиненными. Механика поллинга и хранения —
 * в `useTerminalNotifications`.
 */
import { useMemo } from "react";
import { listTasks } from "@/api/server/misc";
import { isTerminalTaskStatus } from "@/api/server/types";
import type { TaskRead } from "@/api/server/types";
import { useTerminalNotifications } from "@/api/useTerminalNotifications";
import { useAuthOptional } from "@/contexts/AuthContext";

const POLL_MS = 10_000;
const FETCH_LIMIT = 20;
const STORAGE_PREFIX = "emm.notifications.";

/** Одна строка в центре уведомлений. */
export interface TaskNotification {
  task: TaskRead;
  read: boolean;
}

export interface MyTaskNotifications {
  notifications: TaskNotification[];
  unreadCount: number;
  markAllRead: () => void;
  markRead: (id: string) => void;
}

/** Подпись задачи для toast'а: «<kind> завершена ...». */
function outcomeMessage(task: TaskRead): string {
  if (task.status === "succeeded") {
    return `Задача ${task.kind} завершена успешно`;
  }
  if (task.status === "failed") {
    return `Задача ${task.kind} завершилась с ошибкой`;
  }
  return `Задача ${task.kind} отменена`;
}

export function useMyTaskNotifications(): MyTaskNotifications {
  const userId = useAuthOptional()?.user?.user_id ?? null;

  const { rows, unreadCount, markAllRead, markRead } = useTerminalNotifications<TaskRead>({
    userId,
    storagePrefix: STORAGE_PREFIX,
    pollMs: POLL_MS,
    fetchItems: (id) => listTasks({ limit: FETCH_LIMIT, created_by: id }).then((p) => p.items),
    getId: (t) => t.id,
    getStatus: (t) => t.status,
    isTerminal: isTerminalTaskStatus,
    buildMessage: outcomeMessage,
  });

  const notifications = useMemo<TaskNotification[]>(
    () => rows.map(({ item, read }) => ({ task: item, read })),
    [rows],
  );

  return { notifications, unreadCount, markAllRead, markRead };
}
