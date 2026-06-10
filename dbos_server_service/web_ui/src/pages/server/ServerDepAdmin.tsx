import { useState } from "react";
import {
  Search,
  User,
  Users,
  GitBranch,
  Database,
  Cpu,
  Server as ServerIcon,
  Plus,
  Power,
  ChevronDown,
  Edit3,
  Terminal,
  Trash2,
  UsersRound,
  Network,
  Key,
  History,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Shell } from "@/components/shell/Shell";

interface ServerRow {
  id: string;
  name: string;
  bmc: string;
  age: string;
  ageClass?: string;
  badge: string;
  badgeKind?: "ok" | "warn" | "danger" | "";
  icon: typeof Database;
}

const MY_DEP: ServerRow[] = [
  { id: "srv-db-master-01", name: "srv-db-master-01", bmc: "bmc-rack-A-01", age: "2 мин", badge: "Up", badgeKind: "ok", icon: Database },
  { id: "srv-db-replica-01", name: "srv-db-replica-01", bmc: "bmc-rack-A-02", age: "3 мин", badge: "Up", badgeKind: "ok", icon: Database },
  { id: "srv-db-replica-02", name: "srv-db-replica-02", bmc: "bmc-rack-A-03", age: "2 мин", badge: "Up", badgeKind: "ok", icon: Database },
  { id: "srv-compute-01", name: "srv-compute-01", bmc: "bmc-rack-A-04", age: "1 мин", badge: "Up", badgeKind: "ok", icon: Cpu },
  { id: "srv-compute-02", name: "srv-compute-02", bmc: "bmc-rack-A-05", age: "4 мин", badge: "Up", badgeKind: "ok", icon: Cpu },
  { id: "srv-compute-03", name: "srv-compute-03", bmc: "bmc-rack-A-06", age: "обслуживание", badge: "Maintenance", badgeKind: "warn", icon: Cpu },
  { id: "srv-node-01", name: "srv-node-01", bmc: "bmc-rack-B-01", age: "1 мин", badge: "Up", badgeKind: "ok", icon: ServerIcon },
  { id: "srv-node-02", name: "srv-node-02", bmc: "bmc-rack-B-02", age: "15 мин", badge: "Boot", badgeKind: "", icon: ServerIcon },
  { id: "srv-node-03", name: "srv-node-03", bmc: "bmc-rack-B-03", age: "2 ч назад", ageClass: "text-danger", badge: "Down", badgeKind: "danger", icon: ServerIcon },
  { id: "srv-node-04-old", name: "srv-node-04-old", bmc: "bmc-rack-B-04", age: "списан", badge: "Decommission", badgeKind: "", icon: ServerIcon },
];

const CROSS_DEP = [
  { id: "infra-monitoring-db", name: "infra-monitoring-db", dept: "Инфра", note: "read-only", noteClass: "text-warn", badge: "Up", badgeKind: "ok" as const, icon: Database },
  { id: "dtkk-jira-host", name: "dtkk-jira-host", dept: "ДТКК", note: "shared", badge: "Up", badgeKind: "ok" as const, icon: ServerIcon },
];

export function ServerDepAdmin() {
  const [selected, setSelected] = useState<string>("srv-db-master-01");

  return (
    <Shell breadcrumb="server_service / servers">
      {/* Middle: server list */}
      <section className="w-[340px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder="Поиск серверов..."
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim">
            <span>Группировка:</span>
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>scope (my / my_dep / cross_dep)</option>
              <option>dept</option>
              <option>rack</option>
              <option>status</option>
            </select>
            <span className="ml-auto">12 шт</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <div className="group-header flex items-center gap-2">
            <User className="w-3 h-3" /> my · 0
          </div>
          <div className="px-3 py-2 text-xs text-dim">
            Личных серверов нет. Сервера принадлежат депу.
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Users className="w-3 h-3" /> my_dep · Ядро DBOS · 10
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {MY_DEP.map((row) => {
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
                        <span className="mono">{row.bmc}</span>
                        <span>·</span>
                        <span className={row.ageClass}>{row.age}</span>
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
            })}
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <GitBranch className="w-3 h-3" /> cross_dep (через ACL) · 2
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {CROSS_DEP.map((row) => {
              const Icon = row.icon;
              const active = selected === row.id;
              return (
                <button
                  key={row.id}
                  onClick={() => setSelected(row.id)}
                  className={`cred-row text-left ${active ? "active" : ""}`}
                >
                  <div className="flex items-center gap-2">
                    <Icon className="w-4 h-4 text-dim" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate">{row.name}</div>
                      <div className="text-[11px] text-dim flex items-center gap-2">
                        <span>
                          dept: <b>{row.dept}</b>
                        </span>
                        <span>·</span>
                        <span className={row.noteClass}>{row.note}</span>
                      </div>
                    </div>
                    <span className={`badge badge-${row.badgeKind}`}>
                      {row.badge}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Plus className="w-4 h-4" /> Создать server
          </button>
          <div className="text-[10px] text-dim mt-1 text-center">
            dep_admin может добавлять серверы в свой деп
          </div>
        </div>
      </section>

      {/* Right: workzone */}
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
                scope: <b>my_dep</b>
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

interface ServerOverviewGridProps {
  breadcrumbToSecret: string;
  transport: string;
  binding: { label: string; href: string } | null;
  accountAdminNote: string | null;
}

export function ServerOverviewGrid({
  breadcrumbToSecret,
  transport,
  accountAdminNote,
}: ServerOverviewGridProps) {
  return (
    <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
      {/* State */}
      <div className="surface border border-token rounded-lg p-4 col-span-2">
        <div className="text-xs uppercase tracking-wider text-dim mb-3">
          Состояние
        </div>
        <div className="grid grid-cols-4 gap-4">
          <Meter label="CPU" value="45%" pct={45} color="ok" />
          <Meter label="RAM" value="62%" pct={62} color="warn" />
          <Meter label="Storage" value="78%" pct={78} color="danger" />
          <Meter label="Net" value="12 Mbps" pct={12} color="ok" />
        </div>
      </div>

      {/* Hardware */}
      <div className="surface border border-token rounded-lg p-4">
        <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
          <Cpu className="w-4 h-4" /> Hardware
        </div>
        <div className="text-sm">
          <div className="stat-row">
            <span className="text-dim">CPU</span>
            <span>Intel Xeon Gold 6342 @ 2.8 GHz</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Cores / threads</span>
            <span>24 / 48</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">RAM</span>
            <span>256 GB DDR4 ECC</span>
          </div>
        </div>
        <table className="w-full text-xs mt-3">
          <thead className="text-left text-dim uppercase">
            <tr>
              <th className="pb-1 pr-2">Диск</th>
              <th className="pb-1 pr-2">Тип</th>
              <th className="pb-1">Объём</th>
            </tr>
          </thead>
          <tbody className="mono">
            <tr className="border-t border-token">
              <td className="py-1">nvme0n1</td>
              <td>NVMe</td>
              <td>1.92 TB</td>
            </tr>
            <tr className="border-t border-token">
              <td className="py-1">nvme1n1</td>
              <td>NVMe</td>
              <td>1.92 TB</td>
            </tr>
            <tr className="border-t border-token">
              <td className="py-1">sda</td>
              <td>SAS</td>
              <td>3.84 TB</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* OS accounts */}
      <div className="surface border border-token rounded-lg p-4">
        <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
          <UsersRound className="w-4 h-4" /> OS accounts
        </div>
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">Пользователь</th>
              <th className="pb-2 pr-3">Cred</th>
              <th className="pb-2">Last login</th>
            </tr>
          </thead>
          <tbody>
            {[
              { user: "root", cred: "root-vault", kind: "ok", when: "3 ч" },
              { user: "ansible", cred: "ansible-ssh", kind: "ok", when: "12 мин" },
              { user: "monitor", cred: "monitor-key", kind: "", when: "1 мин" },
              { user: "deploy", cred: "deploy-ci", kind: "warn", when: "2 дн" },
              { user: "postgres", cred: "pg-local", kind: "", when: "5 мин" },
            ].map((r) => (
              <tr key={r.user} className="border-t border-token">
                <td className="py-2 mono">{r.user}</td>
                <td>
                  <Link
                    to={breadcrumbToSecret}
                    className={`badge${r.kind ? ` badge-${r.kind}` : ""}`}
                  >
                    {r.cred}
                  </Link>
                </td>
                <td className="text-dim text-xs">{r.when}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* IPMI */}
      <div className="surface border border-token rounded-lg p-4">
        <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
          <Network className="w-4 h-4" /> IPMI / BMC
        </div>
        <div className="text-sm">
          <div className="stat-row">
            <span className="text-dim">BMC host</span>
            <span className="mono">bmc-rack-A-01.dbos.local</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">MAC</span>
            <span className="mono">a4:bf:01:7e:2c:9d</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Last reachable</span>
            <span>2 мин назад</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Power state</span>
            <span className="badge badge-ok">on</span>
          </div>
          <div className="stat-row">
            <span className="text-dim">Transport</span>
            <span className="mono">{transport}</span>
          </div>
        </div>
        <button className="btn mt-3 w-full flex items-center justify-center gap-2">
          <Terminal className="w-4 h-4" /> Open console
        </button>
      </div>

      {/* Bound credentials */}
      <div className="surface border border-token rounded-lg p-4">
        <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
          <Key className="w-4 h-4" /> Привязанные credentials
        </div>
        <div className="text-sm">
          <div className="stat-row">
            <span className="text-dim">IPMI</span>
            <span>
              <Link to={breadcrumbToSecret} className="text-accent mono">
                bmc-rack-A-ipmi
              </Link>
            </span>
          </div>
          <div className="stat-row">
            <span className="text-dim">root</span>
            <span>
              <Link to={breadcrumbToSecret} className="text-accent mono">
                root-vault
              </Link>
            </span>
          </div>
          <div className="stat-row">
            <span className="text-dim">ansible</span>
            <span>
              <Link to={breadcrumbToSecret} className="text-accent mono">
                ansible-ssh
              </Link>
            </span>
          </div>
        </div>
        {accountAdminNote && (
          <div className="text-[11px] text-dim mt-2">{accountAdminNote}</div>
        )}
      </div>

      {/* Recent tasks */}
      <div className="surface border border-token rounded-lg p-4 col-span-2">
        <div className="flex items-center justify-between mb-3">
          <div className="text-xs uppercase tracking-wider text-dim flex items-center gap-2">
            <History className="w-4 h-4" /> Последние tasks
          </div>
          <Link to="/log" className="text-xs text-accent">
            Открыть полный лог →
          </Link>
        </div>
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">Время</th>
              <th className="pb-2 pr-3">Тип</th>
              <th className="pb-2 pr-3">Статус</th>
              <th className="pb-2 pr-3">Actor</th>
              <th className="pb-2">Request</th>
            </tr>
          </thead>
          <tbody className="text-sm">
            {[
              { ts: "15:42:11", op: "power.cycle", stat: "ok", statClass: "text-ok", actor: "alice", req: "req_7e9f..." },
              { ts: "14:18:05", op: "inventory.refresh", stat: "ok", statClass: "text-ok", actor: "alice", req: "req_4ab1..." },
              { ts: "11:02:54", op: "ipmi.probe", stat: "timeout", statClass: "text-warn", actor: "system", req: "req_22c8..." },
              { ts: "2 дн", op: "account.add", stat: "ok", statClass: "text-ok", actor: "alice", req: "req_aa10..." },
              { ts: "7 дн", op: "server.create", stat: "ok", statClass: "text-ok", actor: "alice", req: "req_001f..." },
            ].map((r) => (
              <tr key={r.req} className="border-t border-token">
                <td className="py-2 mono">{r.ts}</td>
                <td>{r.op}</td>
                <td className={r.statClass}>{r.stat}</td>
                <td>{r.actor}</td>
                <td className="mono text-dim">{r.req}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Meter({
  label,
  value,
  pct,
  color,
}: {
  label: string;
  value: string;
  pct: number;
  color: "ok" | "warn" | "danger";
}) {
  const colorVar =
    color === "ok"
      ? "var(--ok)"
      : color === "warn"
        ? "var(--warn)"
        : "var(--danger)";
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-dim">{label}</span>
        <span className={`text-${color}`}>{value}</span>
      </div>
      <div className="progress">
        <span style={{ width: `${pct}%`, background: colorVar }} />
      </div>
    </div>
  );
}
