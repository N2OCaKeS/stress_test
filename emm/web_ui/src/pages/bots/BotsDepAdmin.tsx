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
import { listBotsWithTotal } from "@/api/auth/bots";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useDeptLabel } from "@/lib/labels";
import { relativeTime } from "@/lib/datetime";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import type { Bot as BotItem } from "@/api/auth/types";
import { BotDetailFullPanel } from "./_botDetailPanel";

// auth_service режет страницу до 200 (MAX_LIMIT). Тянем кап и сигналим
// баннером, если ботов в отделе больше.
const BOTS_PAGE = 200;

const STATUS_FILTER_OPTIONS: DropdownOption[] = [
  { value: "active", label: "active" },
  { value: "disabled", label: "disabled" },
];

/**
 * Department-scoped bots view for dep_admin: показывает ботов только своего
 * отдела. Cross-dep секция — informational заглушка (по бизнес-правилу
 * dep_admin не видит ботов чужих депов).
 */

export function BotsDepAdmin() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  const myDept = personaDeptId(persona);
  const myDeptLabel = useDeptLabel(myDept);
  const myUsername = persona.username;

  const [refreshTick, setRefreshTick] = useState(0);
  const botsQ = useQuery(
    () =>
      myDept
        ? listBotsWithTotal({ limit: BOTS_PAGE, department_id: myDept })
        : Promise.resolve({ items: [] as BotItem[], total: 0 }),
    [myDept, refreshTick],
    { enabled: !mockMode && !!myDept },
  );

  const allBots = useMemo(() => botsQ.data?.items ?? [], [botsQ.data]);
  const loadedBots = allBots.length;
  const depTotal = botsQ.data?.total ?? loadedBots;

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  // Поиск/статус применяются к загруженной странице; баннер усечения считает
  // от total до фильтрации.
  const filteredBots = useMemo(() => {
    const term = search.trim().toLowerCase();
    return allBots.filter((b) => {
      if (term && !b.name.toLowerCase().includes(term)) return false;
      if (statusFilter && b.status !== statusFilter) return false;
      return true;
    });
  }, [allBots, search, statusFilter]);

  const myBots = useMemo(
    () => filteredBots.filter((b) => b.created_by === myUsername),
    [filteredBots, myUsername],
  );
  const depBots = filteredBots;

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
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
            <Filter className="w-3 h-3" />
            <Dropdown
              mode="single"
              placeholder="все статусы"
              options={STATUS_FILTER_OPTIONS}
              value={statusFilter}
              onChange={setStatusFilter}
            />
            <span className="ml-auto">
              {depTotal > loadedBots
                ? `${depBots.length} из ${depTotal}`
                : depBots.length}{" "}
              шт · {myDeptLabel}
            </span>
          </div>
          {!botsQ.loading && !botsQ.error && (
            <TruncationNotice
              shown={loadedBots}
              total={depTotal}
              className="mt-2"
            />
          )}
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
            key={selected.id}
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
