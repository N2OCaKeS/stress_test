/**
 * Колокол уведомлений для TopBar. Бейдж — число непрочитанных «моих» задач,
 * клик разворачивает центр уведомлений (поповер порталом).
 */
import { useRef, useState } from "react";
import { Bell } from "lucide-react";
import { useMyTaskNotifications } from "@/api/server/useMyTaskNotifications";
import { NotificationCenter } from "@/components/notifications/NotificationCenter";

export function NotificationBell() {
  const { notifications, unreadCount, markAllRead, markRead } =
    useMyTaskNotifications();
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

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
