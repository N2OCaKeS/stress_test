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
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";

/**
 * Port of bots-dep_admin.html (alice).
 * Middle: bots grouped my / my_dep (Ядро DBOS) / cross_dep.
 * Workzone: dbos_bot_ci_runner overview.
 */
const MY_DEP = [
  { name: "dbos_bot_ci_runner", badge: "worker.operator", badgeKind: "accent" as const, last: "5 мин", status: "active" as const, active: true },
  { name: "dbos_bot_rotation_runner", badge: "server.reader", last: "2 мин", status: "active" as const },
  { name: "dbos_bot_ansible_core", badge: "server.operator", last: "10 мин", status: "active" as const },
  { name: "dbos_bot_secret_sweeper", badge: "secret.reader", last: "1 ч", status: "active" as const },
  { name: "dbos_bot_audit_export", badge: "loging.reader", last: "3 дн", status: "disabled" as const },
];

export function BotsDepAdmin() {
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
            <span className="ml-auto">5 шт · Ядро DBOS</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <div className="group-header flex items-center gap-2">
            <User className="w-3 h-3" /> my · 0
          </div>
          <div className="px-3 py-2 text-xs text-dim italic">
            Лично за вами ботов нет.
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> my_dep · 5
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {MY_DEP.map((row) => (
              <div
                key={row.name}
                className={`cred-row ${row.active ? "active" : ""}`}
              >
                <div className="flex items-center gap-2">
                  <Bot
                    className={`w-4 h-4 ${row.active ? "text-accent" : "text-dim"}`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate mono">{row.name}</div>
                    <div className="text-[11px] text-dim flex items-center gap-2">
                      <span
                        className={`badge ${row.badgeKind === "accent" ? "badge-accent" : ""}`}
                      >
                        {row.badge}
                      </span>
                      <span>·</span>
                      <span>{row.last}</span>
                    </div>
                  </div>
                  <span
                    className={`badge ${row.status === "active" ? "badge-ok" : "badge-warn"}`}
                  >
                    {row.status}
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Building2 className="w-3 h-3" /> cross_dep · 0
          </div>
          <div className="px-3 py-2 text-xs text-dim italic">
            Боты других депов не видны — обращайтесь к bob (account_admin).
          </div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Bot className="w-4 h-4" /> Завести бота
          </button>
          <div className="text-[10px] text-dim mt-1 text-center">
            dep_admin создаёт ботов в рамках Ядро DBOS
          </div>
        </div>
      </aside>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="border-b border-token p-5 flex items-start gap-4">
          <div className="w-12 h-12 rounded surface-2 border border-token flex items-center justify-center">
            <Bot className="w-6 h-6 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate mono">
                dbos_bot_ci_runner
              </h1>
              <span className="badge badge-ok">active</span>
              <span className="badge">bot</span>
              <span className="badge badge-accent">worker.operator</span>
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span>Ядро DBOS</span>
              <span>·</span>
              <span className="mono">bot_3f9d5a712c</span>
              <span>·</span>
              <span>
                purpose: <b>CI pipeline — submit migration jobs</b>
              </span>
            </div>
            <div className="text-xs text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="flex items-center gap-1">
                <User className="w-3 h-3" /> created_by{" "}
                <span className="mono">alice</span>
              </span>
              <span>·</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" /> создан 2026-01-09
              </span>
              <span>·</span>
              <span>last_used 5 мин назад</span>
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
                <StatRow k="name" v={<span className="mono">dbos_bot_ci_runner</span>} />
                <StatRow k="id" v={<span className="mono">bot_3f9d5a712c</span>} />
                <StatRow k="identity_type" v={<span className="mono">bot</span>} />
              </div>
              <div>
                <StatRow k="dept" v="Ядро DBOS" />
                <StatRow k="status" v={<span className="badge badge-ok">active</span>} />
                <StatRow k="created_by" v={<span className="mono">alice</span>} />
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
            <div className="mt-3 text-sm">
              <StatRow k="last rotated" v="2026-04-12 11:30" />
              <StatRow k="expires" v="2026-10-12 (через 4 мес)" />
              <StatRow k="rotated_by" v={<span className="mono">alice</span>} />
            </div>
          </div>

          <div className="surface border border-token rounded-lg p-4">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <User className="w-4 h-4" /> Owner
            </div>
            <div className="text-sm">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-8 h-8 rounded-full bg-accent flex items-center justify-center text-xs font-semibold">
                  AL
                </div>
                <div>
                  <div>
                    alice <span className="badge">dep_admin</span>
                  </div>
                  <div className="text-xs text-dim">
                    Ядро DBOS · вы (текущая сессия)
                  </div>
                </div>
              </div>
              <div className="text-xs text-dim mt-2">
                Бот принадлежит департаменту. Создан вами 2026-01-09 — owner = dept,
                не лично alice.
              </div>
            </div>
          </div>

          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4" /> Service-roles
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">service</th>
                  <th className="pb-2 pr-3">role</th>
                  <th className="pb-2 pr-3">scope</th>
                  <th className="pb-2 pr-3">granted</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {[
                  { svc: "server_worker", role: "operator", scope: "dept=Ядро DBOS", granted: "2026-01-09" },
                  { svc: "server_service", role: "reader", scope: "dept=Ядро DBOS", granted: "2026-01-09" },
                ].map((r) => (
                  <tr key={r.svc} className="border-t border-token">
                    <td className="py-2">
                      <span className="badge badge-accent">{r.svc}</span>
                    </td>
                    <td className="mono">{r.role}</td>
                    <td className="text-dim text-xs">{r.scope}</td>
                    <td className="text-dim text-xs">{r.granted}</td>
                    <td>
                      <button className="btn btn-danger text-xs">revoke</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <Clock className="w-4 h-4" /> Recent calls
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">time</th>
                  <th className="pb-2 pr-3">service</th>
                  <th className="pb-2 pr-3">endpoint</th>
                  <th className="pb-2">result</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { t: "5 мин назад", s: "server_worker", e: "POST /jobs/submit", r: "201" },
                  { t: "17 мин назад", s: "server_worker", e: "GET /jobs/abc123", r: "200" },
                  { t: "28 мин назад", s: "server_service", e: "GET /servers", r: "200" },
                  { t: "42 мин назад", s: "server_worker", e: "POST /jobs/submit", r: "201" },
                  { t: "55 мин назад", s: "server_worker", e: "GET /jobs/def456", r: "200" },
                ].map((row, i) => (
                  <tr key={i} className="border-t border-token">
                    <td className="py-2 text-dim text-xs">{row.t}</td>
                    <td className="mono">{row.s}</td>
                    <td className="mono text-xs">{row.e}</td>
                    <td>
                      <span className="badge badge-ok">{row.r}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </Shell>
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
