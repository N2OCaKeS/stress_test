import { useEffect, useMemo, useState } from "react";
import {
  Search,
  Filter,
  Building2,
  Bot,
  Loader2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { listDepartments } from "@/api/auth/departments";
import { listBots } from "@/api/auth/bots";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { useLabelMaps } from "@/lib/labels";
import type { Bot as BotItem, Department } from "@/api/auth/types";
import { BotDetailFullPanel } from "./_botDetailPanel";

/**
 * Cluster-wide bots view for account_admin: фактический список ботов из
 * auth_service, сгруппированный по реальным отделам. Workzone (правая
 * панель) — полная карточка выбранного бота (token list, roles, allowed
 * services, lifecycle actions).
 */

interface BotGroup {
  deptId: string | "no_dept";
  deptName: string;
  rows: BotItem[];
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  const min = Math.round(diff / 60_000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} ч`;
  const d = Math.round(h / 24);
  return `${d} дн`;
}

export function BotsAccountAdmin() {
  const mockMode = useMockMode();
  const [refreshTick, setRefreshTick] = useState(0);
  const deptsQ = useQuery<Department[]>(
    () => listDepartments(),
    [],
    { enabled: !mockMode },
  );
  const botsQ = useQuery<BotItem[]>(
    () => listBots({ limit: 500 }),
    [refreshTick],
    { enabled: !mockMode },
  );
  const { depts: deptLabels } = useLabelMaps();

  const groups: BotGroup[] = useMemo(() => {
    const bots = botsQ.data ?? [];
    const depts = deptsQ.data ?? [];
    const byDept = new Map<string, BotItem[]>();
    for (const b of bots) {
      const key = b.department_id ?? "no_dept";
      const arr = byDept.get(key) ?? [];
      arr.push(b);
      byDept.set(key, arr);
    }
    const list: BotGroup[] = depts
      .map((d) => ({
        deptId: d.id,
        deptName: deptLabels.get(d.id) ?? d.name,
        rows: byDept.get(d.id) ?? [],
      }))
      .filter((g) => g.rows.length > 0);
    const orphan = byDept.get("no_dept");
    if (orphan && orphan.length > 0) {
      list.push({ deptId: "no_dept", deptName: "без отдела", rows: orphan });
    }
    // Депы без ботов — тоже показываем, чтобы было видно «дев есть, ботов нет».
    for (const d of depts) {
      if (!byDept.has(d.id)) {
        list.push({
          deptId: d.id,
          deptName: deptLabels.get(d.id) ?? d.name,
          rows: [],
        });
      }
    }
    return list;
  }, [botsQ.data, deptsQ.data, deptLabels]);

  const totalBots = (botsQ.data ?? []).length;

  // Selected bot state — поднимаем сюда, чтобы можно было кликать в списке
  // и видеть детали выбранного бота справа.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useMemo(() => {
    const all = botsQ.data ?? [];
    if (selectedId) {
      const found = all.find((b) => b.id === selectedId);
      if (found) return found;
    }
    return all.find((b) => b.status === "active") ?? all[0];
  }, [botsQ.data, selectedId]);

  // Если бот пропал из списка (revoked from list refresh) — сбрасываем selectedId.
  useEffect(() => {
    if (!selectedId) return;
    const all = botsQ.data ?? [];
    if (!all.some((b) => b.id === selectedId)) setSelectedId(null);
  }, [botsQ.data, selectedId]);

  const loading = deptsQ.loading || botsQ.loading;
  const error = deptsQ.error ?? botsQ.error;

  return (
    <Shell breadcrumb="auth_service / bots">
      <aside className="w-[360px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder="Поиск ботов..."
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
            <Filter className="w-3 h-3" />
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>все депы</option>
              {(deptsQ.data ?? []).map((d) => (
                <option key={d.id}>{d.name}</option>
              ))}
            </select>
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>active</option>
              <option>disabled</option>
            </select>
            <span className="ml-auto">{totalBots} шт</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {loading && (
            <div className="px-3 py-2 text-xs text-dim flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> загрузка
            </div>
          )}
          {error && (
            <div className="alert-danger text-[11px] mx-3">{error.message}</div>
          )}
          {!loading && !error && groups.length === 0 && (
            <div className="px-3 py-2 text-xs text-dim italic">
              Депов и ботов нет.
            </div>
          )}
          {groups.map((g, gi) => (
            <div key={g.deptId}>
              <div
                className={`group-header flex items-center gap-2 ${gi > 0 ? "mt-3" : ""}`}
              >
                <Building2 className="w-3 h-3" /> {g.deptName} · {g.rows.length}
              </div>
              {g.rows.length === 0 ? (
                <div className="px-3 py-1 text-[11px] text-dim italic">
                  ботов нет
                </div>
              ) : (
                <div className="px-2 flex flex-col gap-0.5">
                  {g.rows.map((row) => {
                    const isActive = row.id === selected?.id;
                    return (
                      <button
                        key={row.id}
                        className={`cred-row text-left ${isActive ? "active" : ""}`}
                        onClick={() => setSelectedId(row.id)}
                      >
                        <div className="flex items-center gap-2">
                          <Bot
                            className={`w-4 h-4 ${isActive ? "text-accent" : "text-dim"}`}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm truncate mono">{row.name}</div>
                            <div className="text-[11px] text-dim flex items-center gap-2">
                              <span className="badge">
                                {row.allowed_services.length} svc
                              </span>
                              <span>·</span>
                              <span>{relativeTime(row.updated_at ?? row.created_at)}</span>
                            </div>
                          </div>
                          <span
                            className={`badge ${row.status === "active" ? "badge-ok" : "badge-warn"}`}
                          >
                            {row.status}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="border-t border-token p-3">
          <div className="text-[10px] text-dim text-center">
            Создание бота — раздел «Сервисы → auth_service → Боты»
          </div>
        </div>
      </aside>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        {selected ? (
          <BotDetailFullPanel
            bot={selected}
            deptLabel={
              deptLabels.get(selected.department_id) ??
              selected.department_id ??
              "—"
            }
            onChanged={() => setRefreshTick((t) => t + 1)}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-dim text-sm">
            Выберите бота слева — детали появятся здесь.
          </div>
        )}
      </section>
    </Shell>
  );
}
