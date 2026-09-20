/**
 * Поповер-список уведомлений под колоколом — два независимых источника
 * («мои» worker-задачи `server_service` и терминальные тесты своего отдела
 * из `testing_service`) в одном списке, отсортированные по времени. Рендерится
 * порталом в `document.body` и позиционируется по координатам якоря — как
 * HelpTooltip, чтобы не клипаться скроллом рабочих зон и ложиться поверх
 * остального.
 *
 * Клик по строке: помечаем прочитанной и уходим на карточку задачи
 * (`/tasks/:id`) либо на раздел прогонов (`/testing/runs` — у отдельного
 * элемента очереди своей страницы пока нет, см. `pages/testing/runs.tsx`).
 * Кнопка сверху отмечает все прочитанными разом, в обоих источниках.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { formatMskShort } from "@/lib/datetime";
import type { TaskStatus } from "@/api/server/types";
import { Badge, type BadgeKind } from "@/components/ui/Badge";
import { rowKey, rowTime } from "@/components/notifications/rows";
import type { AppNotification } from "@/components/notifications/types";

const PANEL_WIDTH = 360;
const GAP = 6;

type Pos = { top?: number; bottom?: number; left: number; maxHeight: number };

const TASK_STATUS_BADGE: Record<string, BadgeKind> = {
  queued: "neutral",
  running: "warn",
  succeeded: "ok",
  failed: "danger",
  cancelled: "neutral",
};

const QUEUE_STATE_BADGE: Record<string, BadgeKind> = {
  queued: "neutral",
  preparing: "warn",
  ready: "warn",
  running: "warn",
  succeeded: "ok",
  failed: "danger",
  skipped: "neutral",
  paused: "neutral",
};

function statusBadgeKind(status: TaskStatus): BadgeKind {
  return TASK_STATUS_BADGE[status] ?? "neutral";
}

function queueStateBadgeKind(state: string): BadgeKind {
  return QUEUE_STATE_BADGE[state] ?? "neutral";
}

function rowTitle(n: AppNotification): string {
  return n.kind === "worker_task" ? n.task.kind : (n.item.test_code ?? n.item.test_name ?? n.item.test_id);
}

function rowBadge(n: AppNotification) {
  return n.kind === "worker_task"
    ? { kind: statusBadgeKind(n.task.status), label: n.task.status }
    : { kind: queueStateBadgeKind(n.item.state), label: n.item.state };
}

/** Короткий итог: ошибка при провале, иначе пусто. */
function summary(n: AppNotification): string | null {
  if (n.kind === "worker_task" && n.task.status === "failed") {
    return n.task.last_error ?? "Задача завершилась с ошибкой";
  }
  if (n.kind === "test_run" && n.item.state === "failed") {
    return n.item.error ?? "Тест завершился с ошибкой";
  }
  return null;
}

interface NotificationCenterProps {
  /** Якорь (кнопка-колокол), относительно которого позиционируем панель. */
  anchorRef: RefObject<HTMLElement | null>;
  notifications: AppNotification[];
  onClose: () => void;
  onMarkRead: (n: AppNotification) => void;
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

  function openRow(n: AppNotification) {
    onMarkRead(n);
    onClose();
    if (n.kind === "worker_task") navigate(`/tasks/${n.task.id}`);
    // У отдельного элемента очереди своей карточки в UI пока нет — ведём в
    // общий раздел прогонов, найти конкретный тест там несложно.
    else navigate("/testing/runs");
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
        <span className="font-semibold">Уведомления</span>
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
            const badge = rowBadge(n);
            return (
              <li key={rowKey(n)}>
                <button
                  type="button"
                  onClick={() => openRow(n)}
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
                    <span className="truncate flex-1">{rowTitle(n)}</span>
                    <Badge kind={badge.kind}>{badge.label}</Badge>
                  </span>
                  {note && (
                    <span className="text-xs text-danger truncate">{note}</span>
                  )}
                  <span className="text-xs text-dim">{formatMskShort(rowTime(n))}</span>
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
