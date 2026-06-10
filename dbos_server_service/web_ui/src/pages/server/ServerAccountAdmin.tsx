import { useState } from "react";
import {
  Search,
  User,
  Building2,
  Database,
  Cpu,
  Server as ServerIcon,
  Plus,
  Power,
  ChevronDown,
  Edit3,
  Terminal,
  Trash2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { ServerOverviewGrid } from "./ServerDepAdmin";

interface Row {
  id: string;
  name: string;
  dept: string;
  note: string;
  noteClass?: string;
  badge: string;
  badgeKind?: "ok" | "warn" | "danger" | "";
  icon: typeof Database;
}

const CORE: Row[] = [
  { id: "srv-db-master-01", name: "srv-db-master-01", dept: "Ядро DBOS", note: "bmc-A-01", badge: "Up", badgeKind: "ok", icon: Database },
  { id: "srv-db-replica-01", name: "srv-db-replica-01", dept: "Ядро DBOS", note: "3 мин", badge: "Up", badgeKind: "ok", icon: Database },
  { id: "srv-db-replica-02", name: "srv-db-replica-02", dept: "Ядро DBOS", note: "2 мин", badge: "Up", badgeKind: "ok", icon: Database },
  { id: "srv-compute-01", name: "srv-compute-01", dept: "Ядро DBOS", note: "1 мин", badge: "Up", badgeKind: "ok", icon: Cpu },
  { id: "srv-compute-02", name: "srv-compute-02", dept: "Ядро DBOS", note: "обсл.", badge: "Maintenance", badgeKind: "warn", icon: Cpu },
  { id: "srv-node-01", name: "srv-node-01", dept: "Ядро DBOS", note: "1 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-node-02", name: "srv-node-02", dept: "Ядро DBOS", note: "boot", badge: "Boot", badgeKind: "", icon: ServerIcon },
  { id: "srv-node-03", name: "srv-node-03", dept: "Ядро DBOS", note: "2 ч", noteClass: "text-danger", badge: "Down", badgeKind: "danger", icon: ServerIcon },
  { id: "srv-node-04-old", name: "srv-node-04-old", dept: "Ядро DBOS", note: "списан", badge: "Decommission", badgeKind: "", icon: ServerIcon },
  { id: "srv-cache-redis-01", name: "srv-cache-redis-01", dept: "Ядро DBOS", note: "4 мин", badge: "Up", badgeKind: "ok", icon: Database },
];

const DTKK: Row[] = [
  { id: "srv-jira-01", name: "srv-jira-01", dept: "ДТКК", note: "2 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-jira-db-01", name: "srv-jira-db-01", dept: "ДТКК", note: "3 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-conf-01", name: "srv-conf-01", dept: "ДТКК", note: "1 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-build-01", name: "srv-build-01", dept: "ДТКК", note: "обсл.", badge: "Maintenance", badgeKind: "warn", icon: Cpu },
  { id: "srv-build-02", name: "srv-build-02", dept: "ДТКК", note: "5 мин", badge: "Up", badgeKind: "ok", icon: Cpu },
  { id: "srv-wiki-01", name: "srv-wiki-01", dept: "ДТКК", note: "1 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-edge-dtkk-01", name: "srv-edge-dtkk-01", dept: "ДТКК", note: "boot", badge: "Boot", badgeKind: "", icon: ServerIcon },
  { id: "srv-conf-db-01", name: "srv-conf-db-01", dept: "ДТКК", note: "2 мин", badge: "Up", badgeKind: "ok", icon: Database },
];

const INFRA: Row[] = [
  { id: "srv-edge-01", name: "srv-edge-01", dept: "Инфра", note: "1 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-edge-02", name: "srv-edge-02", dept: "Инфра", note: "2 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-edge-03", name: "srv-edge-03", dept: "Инфра", note: "2 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-mon-db-01", name: "srv-mon-db-01", dept: "Инфра", note: "3 мин", badge: "Up", badgeKind: "ok", icon: Database },
  { id: "srv-mon-grafana-01", name: "srv-mon-grafana-01", dept: "Инфра", note: "1 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-router-core-01", name: "srv-router-core-01", dept: "Инфра", note: "offline", noteClass: "text-danger", badge: "Down", badgeKind: "danger", icon: Cpu },
  { id: "srv-router-core-02", name: "srv-router-core-02", dept: "Инфра", note: "1 мин", badge: "Up", badgeKind: "ok", icon: Cpu },
];

export function ServerAccountAdmin() {
  const [selected, setSelected] = useState<string>("srv-db-master-01");

  const renderGroup = (rows: Row[]) =>
    rows.map((row) => {
      const Icon = row.icon;
      const active = selected === row.id;
      return (
        <button
          key={row.id}
          onClick={() => setSelected(row.id)}
          className={`cred-row text-left ${active ? "active" : ""}`}
        >
          <div className="flex items-center gap-2">
            <Icon
              className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`}
            />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate">{row.name}</div>
              <div className="text-[11px] text-dim flex items-center gap-2">
                <span>{row.dept}</span>
                <span>·</span>
                <span className={row.noteClass}>{row.note}</span>
              </div>
            </div>
            <span
              className={`badge${row.badgeKind ? ` badge-${row.badgeKind}` : ""}`}
            >
              {row.badge}
            </span>
          </div>
        </button>
      );
    });

  return (
    <Shell breadcrumb="server_service / servers">
      <section className="w-[340px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder="Поиск по 90 серверам..."
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim">
            <span>Группировка:</span>
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>dept</option>
              <option>scope (my / my_dep / cross_dep)</option>
              <option>rack</option>
              <option>status</option>
            </select>
            <span className="ml-auto">25 / 90</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <div className="group-header flex items-center gap-2">
            <User className="w-3 h-3" /> my · 0
          </div>
          <div className="px-3 py-2 text-xs text-dim">
            У account_admin нет личных серверов.
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> Ядро DBOS · 10
          </div>
          <div className="px-2 flex flex-col gap-0.5">{renderGroup(CORE)}</div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> ДТКК · 8
          </div>
          <div className="px-2 flex flex-col gap-0.5">{renderGroup(DTKK)}</div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> Инфра · 7
          </div>
          <div className="px-2 flex flex-col gap-0.5">{renderGroup(INFRA)}</div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Plus className="w-4 h-4" /> Создать server
          </button>
        </div>
      </section>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="border-b border-token p-5 flex items-start gap-4">
          <div className="w-12 h-12 rounded bg-accent flex items-center justify-center">
            <Database className="w-7 h-7" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold truncate">srv-db-master-01</h1>
              <span className="badge badge-ok">Up</span>
              <span className="text-xs text-dim">
                scope: <b>platform</b>
              </span>
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="mono">srv_8f2a1c0d9b3e</span>
              <span>·</span>
              <span>
                hostname: <b className="mono">db-master-01.dbos.local</b>
              </span>
              <span>·</span>
              <span>
                IPMI: <span className="mono">10.10.20.11</span>
              </span>
              <span>·</span>
              <span>
                dept: <b>Ядро DBOS</b>
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button className="btn flex items-center gap-1">
              <Power className="w-4 h-4" /> Power <ChevronDown className="w-3 h-3" />
            </button>
            <button className="btn">
              <Edit3 className="w-4 h-4 inline-block" /> Edit
            </button>
            <button className="btn">
              <Terminal className="w-4 h-4 inline-block" /> IPMI console
            </button>
            <button className="btn btn-danger">
              <Trash2 className="w-4 h-4 inline-block" /> Delete
            </button>
          </div>
        </div>

        <div className="border-b border-token px-5 flex gap-1">
          <button
            className="px-3 py-2 text-sm border-b-2 -mb-px"
            style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
          >
            Overview
          </button>
          {["Hardware", "Accounts", "IPMI", "Tasks", "Audit"].map((t) => (
            <button
              key={t}
              className="px-3 py-2 text-sm border-b-2 -mb-px border-transparent text-dim hover-bg"
            >
              {t}
            </button>
          ))}
        </div>

        <ServerOverviewGrid
          breadcrumbToSecret="/secret"
          transport="https-verify"
          binding={null}
          accountAdminNote={null}
        />
      </section>
    </Shell>
  );
}
