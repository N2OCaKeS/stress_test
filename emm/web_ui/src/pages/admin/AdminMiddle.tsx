import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import type { AdminItem } from "./adminCatalog";

interface Props {
  items: AdminItem[];
  activeId?: string;
}

function groupBy<T extends { group?: string }>(items: T[]): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const it of items) {
    const k = it.group ?? "";
    const arr = out.get(k);
    if (arr) arr.push(it);
    else out.set(k, [it]);
  }
  return out;
}

function matchesQuery(it: AdminItem, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  if (it.label.toLowerCase().includes(needle)) return true;
  if (it.hint && it.hint.toLowerCase().includes(needle)) return true;
  return false;
}

function Block({
  title,
  items,
  activeId,
  emptyText,
  query,
}: {
  title: string;
  items: AdminItem[];
  activeId?: string;
  emptyText: string;
  query: string;
}) {
  const filtered = useMemo(
    () => items.filter((it) => matchesQuery(it, query)),
    [items, query],
  );
  const grouped = useMemo(() => groupBy(filtered), [filtered]);
  const noMatch = query.length > 0 && filtered.length === 0;

  return (
    <section className="flex-1 min-h-0 flex flex-col">
      <div
        className="px-3 py-2 border-b border-token sticky top-0 z-10"
        style={{ background: "var(--bg-soft)" }}
      >
        <div className="text-xs uppercase tracking-wider text-dim font-semibold">
          {title}
        </div>
        <div className="text-[11px] text-dim">
          {query ? `${filtered.length} / ${items.length} разделов` : `${items.length} разделов`}
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-1">
        {items.length === 0 && (
          <div className="text-xs text-dim px-3 py-4 text-center">
            {emptyText}
          </div>
        )}
        {noMatch && (
          <div className="text-xs text-dim px-3 py-4 text-center">
            Нет совпадений
          </div>
        )}
        {[...grouped.entries()].map(([group, list]) => (
          <div key={group || "_"} className="mb-1">
            {group && (
              <div className="group-header">{group}</div>
            )}
            {list.map((it) => {
              const active = it.id === activeId;
              const Icon = it.icon;
              return (
                <Link
                  key={it.id}
                  to={`/admin/${it.id}`}
                  className={`chip text-left ${active ? "active" : ""}`}
                >
                  <Icon className="w-4 h-4 text-dim shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{it.label}</div>
                    {it.hint && (
                      <div className="text-[11px] text-dim truncate">
                        {it.hint}
                      </div>
                    )}
                  </div>
                </Link>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}

export function AdminMiddle({ items, activeId }: Props) {
  const [query, setQuery] = useState("");

  const clusterItems = items.filter((i) => i.block === "cluster");
  const servicesItems = items.filter((i) => i.block === "services");

  return (
    <aside className="w-[300px] shrink-0 border-r border-token surface flex flex-col min-h-0">
      <div
        className="p-2 border-b border-token sticky top-0 z-20"
        style={{ background: "var(--bg-soft)" }}
      >
        <div className="input-wrap">
          <input
            type="text"
            className="input pr-8"
            placeholder="Поиск разделов..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Search className="input-icon w-4 h-4" />
        </div>
      </div>
      <Block
        title="Сервисы и пользователи"
        items={servicesItems}
        activeId={activeId}
        emptyText="Нет доступных операций"
        query={query}
      />
      <div className="h-px bg-[var(--border)]" />
      <Block
        title="Администрирование кластера"
        items={clusterItems}
        activeId={activeId}
        emptyText="Нет доступных операций"
        query={query}
      />
    </aside>
  );
}
