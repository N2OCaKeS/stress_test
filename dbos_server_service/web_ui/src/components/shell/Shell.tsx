import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { TopBar } from "./TopBar";
import { LeftPanel } from "./LeftPanel";
import { ResizeHandle } from "./ResizeHandle";
import { usePanelWidth, usePanelFlag } from "./usePanelWidth";

interface ShellProps {
  breadcrumb?: string;
  middle?: ReactNode;
  children: ReactNode;
}

const LEFT_DEFAULT = 220;
const LEFT_MIN = 56;
const LEFT_MAX = 360;
const COLLAPSED_THRESHOLD = 80;

const MID_DEFAULT = 300;
const MID_MIN = 200;
const MID_MAX = 600;

/**
 * Shell = TopBar + Left + (optional Middle) + main slot.
 * Middle is passed by pages that need a list (Secrets, Servers, Users, Log...).
 * Home omits Middle and renders directly into the main slot.
 *
 * Left/middle widths are user-resizable and persisted to localStorage.
 * LeftPanel below COLLAPSED_THRESHOLD switches to icons-only rendering.
 * Middle сворачивается целиком в узкую полоску с кнопкой возврата — место
 * отдаётся контенту (например, консоли сервера).
 */
export function Shell({ breadcrumb, middle, children }: ShellProps) {
  const [collapsed, setCollapsed] = usePanelFlag("dbos-left-collapsed", false);
  const [middleCollapsed, setMiddleCollapsed] = usePanelFlag(
    "dbos-middle-collapsed",
    false,
  );
  const [leftWidth, setLeftWidth] = usePanelWidth(
    "dbos-left-width",
    LEFT_DEFAULT,
    LEFT_MIN,
    LEFT_MAX,
  );
  const [middleWidth, setMiddleWidth] = usePanelWidth(
    "dbos-middle-width",
    MID_DEFAULT,
    MID_MIN,
    MID_MAX,
  );

  const effectiveLeft = collapsed ? LEFT_MIN : leftWidth;
  const isCollapsed = collapsed || effectiveLeft < COLLAPSED_THRESHOLD;

  // Drag только меняет ширину. Collapse — только через кнопку в LeftPanel,
  // чтобы не сворачивать панель случайно при перетаскивании.
  const onLeftResize = (next: number) => {
    setLeftWidth(next);
    if (collapsed && next > COLLAPSED_THRESHOLD) setCollapsed(false);
  };

  const toggleCollapsed = () => {
    if (collapsed) {
      setCollapsed(false);
      if (leftWidth < COLLAPSED_THRESHOLD) setLeftWidth(LEFT_DEFAULT);
    } else {
      setCollapsed(true);
    }
  };

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <TopBar breadcrumb={breadcrumb} />
      <div className="flex-1 flex min-h-0">
        <LeftPanel
          width={effectiveLeft}
          collapsed={isCollapsed}
          onToggleCollapsed={toggleCollapsed}
        />
        <ResizeHandle
          width={effectiveLeft}
          onChange={onLeftResize}
          min={LEFT_MIN}
          max={LEFT_MAX}
          resetTo={LEFT_DEFAULT}
          ariaLabel="Resize left panel"
        />
        {middle && !middleCollapsed && (
          <>
            <div
              className="relative shrink-0 min-h-0 flex flex-col overflow-hidden [&>aside]:!w-full [&>section]:!w-full [&>aside]:flex-1 [&>section]:flex-1"
              style={{ width: middleWidth }}
            >
              {middle}
              <button
                type="button"
                onClick={() => setMiddleCollapsed(true)}
                title="Свернуть панель"
                aria-label="Collapse middle panel"
                className="absolute top-1/2 right-0 -translate-y-1/2 z-20 h-10 w-4 flex items-center justify-center rounded-l surface-2 border border-r-0 border-token text-dim hover-bg transition-colors"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
              </button>
            </div>
            <ResizeHandle
              width={middleWidth}
              onChange={setMiddleWidth}
              min={MID_MIN}
              max={MID_MAX}
              resetTo={MID_DEFAULT}
              ariaLabel="Resize middle panel"
            />
          </>
        )}
        {middle && middleCollapsed && (
          <div className="shrink-0 w-7 border-r border-token surface flex flex-col items-center justify-center">
            <button
              type="button"
              onClick={() => setMiddleCollapsed(false)}
              title="Развернуть панель"
              aria-label="Expand middle panel"
              className="btn flex items-center justify-center"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
