import { useEffect, useMemo, useState } from "react";
import {
  Search,
  Filter,
  User,
  Building2,
  Bot,
  Loader2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { listBots } from "@/api/auth/bots";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useDeptLabel } from "@/lib/labels";
import type { Bot as BotItem } from "@/api/auth/types";
import { BotDetailFullPanel } from "./_botDetailPanel";

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

  const [refreshTick, setRefreshTick] = useState(0);
  const botsQ = useQuery<BotItem[]>(
    () => (myDept ? listBots({ limit: 500, department_id: myDept }) : Promise.resolve([])),
    [myDept, refreshTick],
    { enabled: !mockMode && !!myDept },
  );

  const allBots = botsQ.data ?? [];
  const myBots = useMemo(
    () => allBots.filter((b) => b.created_by === myUsername),
    [allBots, myUsername],
  );
  const depBots = allBots;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useMemo(() => {
    if (selectedId) {
      const found = allBots.find((b) => b.id === selectedId);
      if (found) return found;
    }
    return allBots.find((b) => b.status === "active") ?? allBots[0];
  }, [allBots, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    if (!allBots.some((b) => b.id === selectedId)) setSelectedId(null);
  }, [allBots, selectedId]);

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
                <BotRow
                  key={row.id}
                  row={row}
                  activeId={selected?.id}
                  onSelect={() => setSelectedId(row.id)}
                />
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
                <BotRow
                  key={`dep-${row.id}`}
                  row={row}
                  activeId={selected?.id}
                  onSelect={() => setSelectedId(row.id)}
                />
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
          <div className="text-[10px] text-dim text-center">
            Создание бота — раздел «Сервисы → auth_service → Боты»
          </div>
        </div>
      </aside>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        {selected ? (
          <BotDetailFullPanel
            bot={selected}
            deptLabel={myDeptLabel}
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

function BotRow({
  row,
  activeId,
  onSelect,
}: {
  row: BotItem;
  activeId: string | undefined;
  onSelect: () => void;
}) {
  const isActive = row.id === activeId;
  return (
    <button
      className={`cred-row text-left ${isActive ? "active" : ""}`}
      onClick={onSelect}
    >
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
    </button>
  );
}
