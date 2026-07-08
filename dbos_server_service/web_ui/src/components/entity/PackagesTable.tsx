/**
 * Общая таблица установленных пакетов для сервера и ВМ. Один и тот же вид:
 * клиентский фильтр по имени (подстрока или `*`-маска), колонки
 * Название / Версия / Архитектура и состояния (загрузка / ошибка / пусто /
 * ничего не найдено). Различается только источник данных: у сервера — свежий
 * SSH-probe воркера, у ВМ — последний снятый срез гостя.
 */
import { useMemo } from "react";
import { AlertCircle } from "lucide-react";

export interface PackageItem {
  name: string;
  version?: string | null;
  arch?: string | null;
}

/**
 * Клиентский фильтр пакета по имени: подстрока по умолчанию, `*` — маска
 * (glob), матч по всей строке. Регистронезависимо.
 */
function pkgNameMatches(name: string, filter: string): boolean {
  const f = filter.trim().toLowerCase();
  if (!f) return true;
  const n = name.toLowerCase();
  if (f.includes("*")) {
    const escaped = f
      .split("*")
      .map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
      .join(".*");
    return new RegExp(`^${escaped}$`).test(n);
  }
  return n.includes(f);
}

export function PackagesTable({
  items,
  filter,
  onFilter,
  loading = false,
  error,
  onRetry,
  emptyText = "Данных о пакетах пока нет.",
}: {
  items: PackageItem[];
  filter: string;
  onFilter: (v: string) => void;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyText?: string;
}) {
  const filtered = useMemo(
    () => items.filter((p) => pkgNameMatches(p.name, filter)),
    [items, filter],
  );

  return (
    <div className="flex flex-col gap-3">
      {items.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            className="input mono text-xs"
            style={{ minWidth: 220 }}
            placeholder="фильтр по имени (* — маска)"
            value={filter}
            onChange={(e) => onFilter(e.target.value)}
          />
          {filter.trim() && (
            <span className="text-[11px] text-dim">
              показано {filtered.length} из {items.length}
            </span>
          )}
        </div>
      )}

      {loading ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : error && items.length === 0 ? (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>Список пакетов не загрузился</div>
            {onRetry && (
              <button className="btn btn-ghost mt-2" onClick={onRetry}>
                Повторить
              </button>
            )}
          </div>
        </div>
      ) : items.length === 0 ? (
        <div className="text-xs text-dim">{emptyText}</div>
      ) : filtered.length === 0 ? (
        <div className="text-xs text-dim">
          Ничего не найдено по фильтру «{filter.trim()}».
        </div>
      ) : (
        <div className="surface-2 border border-token rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase text-dim border-b border-token">
                <th className="text-left px-3 py-2 font-medium">Название</th>
                <th className="text-left px-3 py-2 font-medium">Версия</th>
                <th className="text-left px-3 py-2 font-medium">Архитектура</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <tr
                  key={`${p.name}-${p.version ?? ""}-${p.arch ?? ""}`}
                  className="border-b border-token last:border-b-0"
                >
                  <td className="px-3 py-1.5 mono text-xs">{p.name}</td>
                  <td className="px-3 py-1.5 mono text-xs">{p.version ?? "—"}</td>
                  <td className="px-3 py-1.5 mono text-xs text-dim">
                    {p.arch ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
