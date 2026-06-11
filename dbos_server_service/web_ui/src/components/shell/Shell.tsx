import type { ReactNode } from "react";
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
 */
export function Shell({ breadcrumb, middle, children }: ShellProps) {
  const [collapsed, setCollapsed] = usePanelFlag("dbos-left-collapsed", false);
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
        {middle && (
          <>
            <div
              className="shrink-0 min-h-0 flex flex-col overflow-hidden [&>aside]:!w-full [&>section]:!w-full [&>aside]:flex-1 [&>section]:flex-1"
              style={{ width: middleWidth }}
            >
              {middle}
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
        {children}
      </div>
    </div>
  );
}
