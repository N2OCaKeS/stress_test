import { useMemo } from "react";
import {
  Search,
  Filter,
  Building2,
  Bot,
  KeyRound,
  RotateCw,
  EyeOff,
  Trash2,
  Eye,
  User,
  Clock,
  ShieldCheck,
  Loader2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { listDepartments } from "@/api/auth/departments";
import { listBots } from "@/api/auth/bots";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { useLabelMaps } from "@/lib/labels";
import type { Bot as BotItem, Department } from "@/api/auth/types";

/**
 * Cluster-wide bots view for account_admin: фактический список ботов из
 * auth_service, сгруппированный по реальным отделам. Workzone (правая
 * панель) — placeholder детальной карточки выбранного бота; пока что
 * показывает первого бота в списке.
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
  const deptsQ = useQuery<Department[]>(
    () => listDepartments(),
    [],
    { enabled: !mockMode },
  );
  const botsQ = useQuery<BotItem[]>(
    () => listBots({ limit: 500 }),
    [],
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
        deptName: deptLabels.get(d.id) ?? d.display_name ?? d.name,
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
          deptName: deptLabels.get(d.id) ?? d.display_name ?? d.name,
          rows: [],
        });
      }
    }
    return list;
  }, [botsQ.data, deptsQ.data, deptLabels]);

  const totalBots = (botsQ.data ?? []).length;
  const activeBot = useMemo(
    () => (botsQ.data ?? []).find((b) => b.status === "active") ?? (botsQ.data ?? [])[0],
    [botsQ.data],
  );

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
                <option key={d.id}>{d.display_name ?? d.name}</option>
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
                  {g.rows.map((row, idx) => {
                    const isActive = row.id === activeBot?.id;
                    return (
                      <div
                        key={row.id || `${g.deptId}-${idx}`}
                        className={`cred-row ${isActive ? "active" : ""}`}
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
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Bot className="w-4 h-4" /> Создать бота
          </button>
          <div className="text-[10px] text-dim mt-1 text-center">
            account_admin видит ботов всех депов
          </div>
        </div>
      </aside>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <BotDetailPanel bot={activeBot} deptLabels={deptLabels} />
      </section>
    </Shell>
  );
}

function BotDetailPanel({
  bot,
  deptLabels,
}: {
  bot: BotItem | undefined;
  deptLabels: Map<string, string>;
}) {
  if (!bot) {
    return (
      <div className="flex-1 flex items-center justify-center text-dim text-sm">
        Выберите бота слева — детали появятся здесь.
      </div>
    );
  }
  const deptName = deptLabels.get(bot.department_id) ?? bot.department_id;
  return (
    <>
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded surface-2 border border-token flex items-center justify-center">
          <Bot className="w-6 h-6 text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">{bot.name}</h1>
            <span
              className={`badge ${bot.status === "active" ? "badge-ok" : "badge-warn"}`}
            >
              {bot.status}
            </span>
            <span className="badge">bot</span>
            {bot.allowed_services.slice(0, 1).map((s) => (
              <span key={s} className="badge badge-accent">
                {s}
              </span>
            ))}
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span>{deptName}</span>
            <span>·</span>
            <span className="mono">{bot.id}</span>
            {bot.description && (
              <>
                <span>·</span>
                <span>{bot.description}</span>
              </>
            )}
          </div>
          <div className="text-xs text-dim mt-1 flex items-center gap-3 flex-wrap">
            {bot.created_by && (
              <span className="flex items-center gap-1">
                <User className="w-3 h-3" /> created_by{" "}
                <span className="mono">{bot.created_by}</span>
              </span>
            )}
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" /> создан{" "}
              {bot.created_at.slice(0, 10)}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <button className="btn flex items-center gap-1">
            <RotateCw className="w-4 h-4" /> Rotate token
          </button>
          <button className="btn btn-danger flex items-center gap-1">
            <EyeOff className="w-4 h-4" /> Disable
          </button>
          <button className="btn btn-danger flex items-center gap-1">
            <Trash2 className="w-4 h-4" /> Delete
          </button>
        </div>
      </div>

      <div className="border-b border-token px-5 flex gap-1 flex-wrap">
        {["Overview", "Token", "Service-roles", "Audit"].map((tab, i) => (
          <button
            key={tab}
            className={`px-3 py-2 text-sm border-b-2 -mb-px ${
              i === 0 ? "border-accent text-accent" : "border-transparent text-dim"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <Bot className="w-4 h-4" /> Идентификация
          </div>
          <div className="grid grid-cols-2 gap-x-6 text-sm">
            <div>
              <StatRow k="name" v={<span className="mono">{bot.name}</span>} />
              <StatRow k="id" v={<span className="mono">{bot.id}</span>} />
              <StatRow k="identity_type" v={<span className="mono">bot</span>} />
            </div>
            <div>
              <StatRow k="dept" v={deptName} />
              <StatRow
                k="status"
                v={
                  <span
                    className={`badge ${bot.status === "active" ? "badge-ok" : "badge-warn"}`}
                  >
                    {bot.status}
                  </span>
                }
              />
              <StatRow
                k="created_by"
                v={<span className="mono">{bot.created_by ?? "—"}</span>}
              />
            </div>
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <KeyRound className="w-4 h-4" /> Token
          </div>
          <div className="surface-2 border border-token rounded p-3 mono text-sm flex items-center justify-between">
            <span className="tracking-[4px] select-none">dbos_bot_***...***</span>
            <button className="btn text-xs flex items-center gap-1">
              <Eye className="w-3 h-3" /> reveal
            </button>
          </div>
          <div className="mt-3 text-xs text-dim">
            Список выпущенных токенов и lifecycle — на вкладке Token (TODO).
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4" /> Allowed services
          </div>
          <div className="flex flex-wrap gap-1">
            {bot.allowed_services.length === 0 ? (
              <span className="text-xs text-dim italic">нет</span>
            ) : (
              bot.allowed_services.map((s) => (
                <span key={s} className="badge badge-accent mono">
                  {s}
                </span>
              ))
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[160px_1fr] gap-2 py-1.5 border-b border-dashed border-token text-sm last:border-b-0">
      <span className="text-dim">{k}</span>
      <span>{v}</span>
    </div>
  );
}
