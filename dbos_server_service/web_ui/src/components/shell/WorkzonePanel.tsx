import type { ReactNode } from "react";

interface WorkzonePanelProps {
  header?: ReactNode;
  tabs?: { value: string; label: string }[];
  activeTab?: string;
  onTabChange?: (value: string) => void;
  children: ReactNode;
}

export function WorkzonePanel({
  header,
  tabs,
  activeTab,
  onTabChange,
  children,
}: WorkzonePanelProps) {
  return (
    <section className="flex-1 flex flex-col min-w-0 min-h-0">
      {header && (
        <div className="border-b border-token surface px-4 py-3">{header}</div>
      )}
      {tabs && tabs.length > 0 && (
        <div className="border-b border-token flex items-center gap-1 px-3">
          {tabs.map((t) => (
            <button
              key={t.value}
              onClick={() => onTabChange?.(t.value)}
              className={`px-3 py-2 text-sm border-b-2 transition-colors ${
                activeTab === t.value
                  ? "border-accent text-accent"
                  : "border-transparent text-dim hover:text-text"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 overflow-y-auto">{children}</div>
    </section>
  );
}
