import { useMemo } from "react";
import {
  Search,
  Filter,
  User,
  Building2,
  Bot,
  KeyRound,
  RotateCw,
  EyeOff,
  Trash2,
  Eye,
  Clock,
  ShieldCheck,
  Loader2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { listBots } from "@/api/auth/bots";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useDeptLabel } from "@/lib/labels";
import type { Bot as BotItem } from "@/api/auth/types";

/**
 * Department-scoped bots view for dep_admin: показывает ботов только своего
 * отдела. Cross-dep секция — informational заглушка (по бизнес-правилу
 * dep_admin не видит ботов чужих депов).
 */

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

export function BotsDepAdmin() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  const myDept = personaDeptId(persona);
  const myDeptLabel = useDeptLabel(myDept);
  const myUsername = persona.username;

  const botsQ = useQuery<BotItem[]>(
    () => (myDept ? listBots({ limit: 500, department_id: myDept }) : Promise.resolve([])),
    [myDept],
    { enabled: !mockMode && !!myDept },
  );

  const allBots = botsQ.data ?? [];
  const myBots = useMemo(
    () => allBots.filter((b) => b.created_by === myUsername),
    [allBots, myUsername],
  );
  const depBots = allBots;
  const activeBot = useMemo(
    () => allBots.find((b) => b.status === "active") ?? allBots[0],
    [allBots],
  );

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
              <option>все статусы</option>
              <option>active</option>
              <option>disabled</option>
            </select>
            <span className="ml-auto">
              {depBots.length} шт · {myDeptLabel}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {botsQ.loading && (
            <div className="px-3 py-2 text-xs text-dim flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> загрузка
            </div>
          )}
          {botsQ.error && (
            <div className="alert-danger text-[11px] mx-3">{botsQ.error.message}</div>
          )}

          <div className="group-header flex items-center gap-2">
            <User className="w-3 h-3" /> my · {myBots.length}
          </div>
          {myBots.length === 0 ? (
            <div className="px-3 py-2 text-xs text-dim italic">
              Лично за вами ботов нет.
            </div>
          ) : (
            <div className="px-2 flex flex-col gap-0.5">
              {myBots.map((row) => (
                <BotRow key={row.id} row={row} activeId={activeBot?.id} />
              ))}
            </div>
          )}

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> my_dep · {depBots.length}
          </div>
          {depBots.length === 0 ? (
            <div className="px-3 py-2 text-xs text-dim italic">
              В отделе ботов нет.
            </div>
          ) : (
            <div className="px-2 flex flex-col gap-0.5">
              {depBots.map((row) => (
                <BotRow key={`dep-${row.id}`} row={row} activeId={activeBot?.id} />
              ))}
            </div>
          )}

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> cross_dep · 0
          </div>
          <div className="px-3 py-2 text-xs text-dim italic">
            Боты других депов не видны — обращайтесь к account_admin.
          </div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Bot className="w-4 h-4" /> Завести бота
          </button>
          <div className="text-[10px] text-dim mt-1 text-center">
            dep_admin создаёт ботов в рамках {myDeptLabel}
          </div>
        </div>
      </aside>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <BotDetailPanel bot={activeBot} deptLabel={myDeptLabel} />
      </section>
    </Shell>
  );
}

function BotRow({ row, activeId }: { row: BotItem; activeId: string | undefined }) {
  const isActive = row.id === activeId;
  return (
    <div className={`cred-row ${isActive ? "active" : ""}`}>
      <div className="flex items-center gap-2">
        <Bot className={`w-4 h-4 ${isActive ? "text-accent" : "text-dim"}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate mono">{row.name}</div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <span className="badge">{row.allowed_services.length} svc</span>
            <span>·</span>
            <span>{relativeTime(row.updated_at ?? row.created_at)}</span>
          </div>
        </div>
        <span className={`badge ${row.status === "active" ? "badge-ok" : "badge-warn"}`}>
          {row.status}
        </span>
      </div>
    </div>
  );
}

function BotDetailPanel({
  bot,
  deptLabel,
}: {
  bot: BotItem | undefined;
  deptLabel: string;
}) {
  if (!bot) {
    return (
      <div className="flex-1 flex items-center justify-center text-dim text-sm">
        Выберите бота слева — детали появятся здесь.
      </div>
    );
  }
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
            <span>{deptLabel}</span>
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
              <Clock className="w-3 h-3" /> создан {bot.created_at.slice(0, 10)}
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
              <StatRow k="dept" v={deptLabel} />
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
