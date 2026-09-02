import type { ReactNode } from "react";

export interface TabSpec {
  id: string;
  label: ReactNode;
  icon?: ReactNode;
  count?: number;
}

interface TabsProps {
  tabs: TabSpec[];
  active: string;
  onChange: (id: string) => void;
  /** Wrap on overflow (used by wide multi-tab bars). */
  wrap?: boolean;
  /** Outer wrapper className override (border, padding). */
  className?: string;
}

/**
 * Underline-style tab bar shared by detail screens.
 *
 * Visual: thin bottom-border row; the active tab gets an accent-coloured
 * underline (`border-b-2`) and accent text. Matches the inline pattern that
 * was previously copy-pasted across UserDetail / GroupDetail / BotDetail.
 */
export function Tabs({ tabs, active, onChange, wrap, className }: TabsProps) {
  const wrapperCls =
    className ??
    `border-b border-token px-5 flex gap-1 shrink-0${wrap ? " flex-wrap" : ""}`;
  return (
    <div className={wrapperCls}>
      {tabs.map((t) => {
        const isActive = t.id === active;
        return (
          <button
            key={t.id}
            onClick={() => onChange(t.id)}
            className={`px-4 py-2 text-sm border-b-2 -mb-px flex items-center gap-2 ${
              isActive ? "border-accent text-accent" : "border-transparent text-dim"
            }`}
          >
            {t.icon}
            {t.label}
            {typeof t.count === "number" && (
              <span className="text-xs text-dim">· {t.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
