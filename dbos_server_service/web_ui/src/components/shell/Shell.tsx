import type { ReactNode } from "react";
import { TopBar } from "./TopBar";
import { LeftPanel } from "./LeftPanel";

interface ShellProps {
  breadcrumb?: string;
  middle?: ReactNode;
  children: ReactNode;
}

/**
 * Shell = TopBar + Left + (optional Middle) + main slot.
 * Middle is passed by pages that need a list (Secrets, Servers, Users, Log...).
 * Home omits Middle and renders directly into the main slot.
 */
export function Shell({ breadcrumb, middle, children }: ShellProps) {
  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <TopBar breadcrumb={breadcrumb} />
      <div className="flex-1 flex min-h-0">
        <LeftPanel />
        {middle}
        {children}
      </div>
    </div>
  );
}
