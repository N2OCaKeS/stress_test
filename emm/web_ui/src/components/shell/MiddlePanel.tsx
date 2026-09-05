import { Search } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Dropdown } from "@/components/ui/Dropdown";

export interface MiddleItem {
  id: string;
  title: ReactNode;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  onClick?: () => void;
  active?: boolean;
}

interface MiddlePanelProps {
  title: string;
  items: MiddleItem[];
  groups?: { value: string; label: string }[];
  selectedGroup?: string;
  onGroupChange?: (group: string) => void;
  placeholder?: string;
  footer?: ReactNode;
}

export function MiddlePanel({
  title,
  items,
  groups,
  selectedGroup,
  onGroupChange,
  placeholder = "Поиск...",
  footer,
}: MiddlePanelProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (!query.trim()) return items;
    const q = query.toLowerCase();
    return items.filter((it) => {
      const text =
        typeof it.title === "string" ? it.title : it.id;
      return text.toLowerCase().includes(q);
    });
  }, [items, query]);

  return (
    <aside className="w-[300px] shrink-0 border-r border-token surface flex flex-col min-h-0">
      <div className="p-3 border-b border-token">
        <div className="flex items-center justify-between mb-2">
          <div className="text-sm font-semibold">{title}</div>
          <div className="text-xs text-dim">{filtered.length}</div>
        </div>
        <div className="relative">
          <Search className="w-4 h-4 absolute left-2 top-1/2 -translate-y-1/2 text-dim" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={placeholder}
            className="w-full surface-2 border border-token rounded pl-7 pr-2 py-1 text-sm focus:outline-none focus:border-accent"
          />
        </div>
        {groups && (
          <Dropdown
            mode="single"
            className="mt-2 w-full"
            options={groups.map((g) => ({ value: g.value, label: g.label }))}
            value={selectedGroup ?? ""}
            onChange={(v) => onGroupChange?.(v)}
          />
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-0.5">
        {filtered.map((it) => (
          <button
            key={it.id}
            onClick={it.onClick}
            className={`chip text-left ${it.active ? "active" : ""}`}
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{it.title}</div>
              {it.subtitle && (
                <div className="text-[11px] text-dim truncate">
                  {it.subtitle}
                </div>
              )}
            </div>
            {it.trailing}
          </button>
        ))}
        {filtered.length === 0 && (
          <div className="text-xs text-dim px-3 py-4 text-center">
            Ничего не найдено
          </div>
        )}
      </div>

      {footer && (
        <div className="p-2 border-t border-token">{footer}</div>
      )}
    </aside>
  );
}
