/**
 * Поповер-список «моих» уведомлений под колоколом. Рендерится порталом в
 * `document.body` и позиционируется по координатам якоря — как HelpTooltip,
 * чтобы не клипаться скроллом рабочих зон и ложиться поверх остального.
 *
 * Клик по строке: помечаем прочитанной и уходим на карточку задачи
 * (`/tasks/:id`). Кнопка сверху отмечает все прочитанными разом.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { formatMskShort } from "@/lib/datetime";
import type { TaskNotification } from "@/api/server/useMyTaskNotifications";
import type { TaskStatus } from "@/api/server/types";

const PANEL_WIDTH = 360;
const GAP = 6;

type Pos = { top?: number; bottom?: number; left: number; maxHeight: number };

const STATUS_BADGE: Record<string, string> = {
  queued: "badge",
  running: "badge badge-warn",
  succeeded: "badge badge-ok",
  failed: "badge badge-danger",
  cancelled: "badge",
};

function statusBadgeClass(status: TaskStatus): string {
  return STATUS_BADGE[status] ?? "badge";
}

/** Короткий итог: ошибка при failed, иначе пусто. */
function summary(n: TaskNotification): string | null {
  if (n.task.status === "failed") {
    return n.task.last_error ?? "Задача завершилась с ошибкой";
  }
  return null;
}

interface NotificationCenterProps {
  /** Якорь (кнопка-колокол), относительно которого позиционируем панель. */
  anchorRef: RefObject<HTMLElement | null>;
  notifications: TaskNotification[];
  onClose: () => void;
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
}

export function NotificationCenter({
  anchorRef,
  notifications,
  onClose,
  onMarkRead,
  onMarkAllRead,
}: NotificationCenterProps) {
  const navigate = useNavigate();
  const panelRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Pos | null>(null);

  const place = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const r = anchor.getBoundingClientRect();
    // Прижимаем правый край панели к правому краю якоря, но не даём вылезти
    // за левый край окна.
    const left = Math.max(GAP, Math.min(r.right - PANEL_WIDTH, window.innerWidth - PANEL_WIDTH - GAP));
    const spaceBelow = window.innerHeight - r.bottom - GAP;
    const spaceAbove = r.top - GAP;
    // Открываем вниз, если снизу места не меньше, чем сверху; иначе разворачиваем
    // вверх, чтобы панель не уходила за нижний край. Высоту в любом случае
    // ограничиваем доступным местом — длинный список скроллится внутри.
    if (spaceBelow >= spaceAbove) {
      setPos({ top: r.bottom + GAP, left, maxHeight: Math.max(GAP, spaceBelow) });
    } else {
      setPos({ bottom: window.innerHeight - r.top + GAP, left, maxHeight: Math.max(GAP, spaceAbove) });
    }
  }, [anchorRef]);

  useLayoutEffect(() => {
    place();
  }, [place]);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      const insidePanel = panelRef.current?.contains(t);
      const insideAnchor = anchorRef.current?.contains(t);
      if (!insidePanel && !insideAnchor) onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [anchorRef, onClose, place]);

  function openTask(id: string) {
    onMarkRead(id);
    onClose();
    navigate(`/tasks/${id}`);
  }

  function openAllTasks() {
    onClose();
    navigate("/server/tasks");
  }

  if (!pos) return null;

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Уведомления о задачах"
      style={{
        position: "fixed",
        top: pos.top,
        bottom: pos.bottom,
        left: pos.left,
        width: PANEL_WIDTH,
        maxHeight: pos.maxHeight,
      }}
      className="z-[1000] surface border border-token rounded shadow-lg text-sm text-text flex flex-col"
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-token shrink-0">
        <span className="font-semibold">Мои задачи</span>
        <button
          type="button"
          className="text-xs text-dim hover:text-accent disabled:opacity-50"
          onClick={onMarkAllRead}
          disabled={notifications.every((n) => n.read)}
        >
          Отметить все прочитанными
        </button>
      </div>

      {notifications.length === 0 ? (
        <div className="px-3 py-6 text-center text-dim text-xs">
          Пока нет уведомлений
        </div>
      ) : (
        <ul className="flex-1 min-h-0 overflow-auto">
          {notifications.map((n) => {
            const note = summary(n);
            return (
              <li key={n.task.id}>
                <button
                  type="button"
                  onClick={() => openTask(n.task.id)}
                  className={`w-full text-left px-3 py-2 border-b border-token hover-bg flex flex-col gap-1 ${
                    n.read ? "" : "font-medium"
                  }`}
                >
                  <span className="flex items-center gap-2">
                    {!n.read && (
                      <span
                        aria-hidden
                        className="w-1.5 h-1.5 rounded-full bg-accent shrink-0"
                      />
                    )}
                    <span className="truncate flex-1">{n.task.kind}</span>
                    <span className={statusBadgeClass(n.task.status)}>
                      {n.task.status}
                    </span>
                  </span>
                  {note && (
                    <span className="text-xs text-danger truncate">{note}</span>
                  )}
                  <span className="text-xs text-dim">
                    {formatMskShort(n.task.finished_at ?? n.task.created_at)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="px-3 py-2 border-t border-token text-center shrink-0">
        <button
          type="button"
          className="text-xs text-accent hover:underline"
          onClick={openAllTasks}
        >
          Все задачи →
        </button>
      </div>
    </div>,
    document.body,
  );
}
