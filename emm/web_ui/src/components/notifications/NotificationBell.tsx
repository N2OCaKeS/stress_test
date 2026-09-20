/**
 * Колокол уведомлений для TopBar. Объединяет два независимых поллера в один
 * список: «мои» worker-задачи `server_service` и терминальные тесты своего
 * отдела из `testing_service` (см. `useDepartmentRunNotifications`) — умышленно
 * один колокол на оба источника, а не два разных.
 */
import { useMemo, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { useMyTaskNotifications } from "@/api/server/useMyTaskNotifications";
import { useDepartmentRunNotifications } from "@/api/testing/useDepartmentRunNotifications";
import { NotificationCenter } from "@/components/notifications/NotificationCenter";
import { rowTime } from "@/components/notifications/rows";
import type { AppNotification } from "@/components/notifications/types";

export function NotificationBell() {
  const workerTasks = useMyTaskNotifications();
  const departmentRuns = useDepartmentRunNotifications();
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  const notifications = useMemo<AppNotification[]>(() => {
    const merged: AppNotification[] = [
      ...workerTasks.notifications.map((n) => ({ kind: "worker_task" as const, ...n })),
      ...departmentRuns.notifications.map((n) => ({ kind: "test_run" as const, ...n })),
    ];
    return merged.sort((a, b) => rowTime(b).localeCompare(rowTime(a)));
  }, [workerTasks.notifications, departmentRuns.notifications]);

  const unreadCount = workerTasks.unreadCount + departmentRuns.unreadCount;

  function markRead(n: AppNotification) {
    if (n.kind === "worker_task") workerTasks.markRead(n.task.id);
    else departmentRuns.markRead(n.item.id);
  }

  function markAllRead() {
    workerTasks.markAllRead();
    departmentRuns.markAllRead();
  }

  const badge = unreadCount > 99 ? "99+" : String(unreadCount);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        aria-label={
          unreadCount > 0
            ? `Уведомления: ${unreadCount} непрочитанных`
            : "Уведомления"
        }
        aria-expanded={open}
        className="relative inline-flex items-center justify-center w-8 h-8 rounded hover-bg text-dim hover:text-accent"
        onClick={() => setOpen((v) => !v)}
      >
        <Bell className="w-4 h-4" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-danger text-white text-[10px] leading-4 text-center font-medium">
            {badge}
          </span>
        )}
      </button>
      {open && (
        <NotificationCenter
          anchorRef={btnRef}
          notifications={notifications}
          onClose={() => setOpen(false)}
          onMarkRead={markRead}
          onMarkAllRead={markAllRead}
        />
      )}
    </>
  );
}
