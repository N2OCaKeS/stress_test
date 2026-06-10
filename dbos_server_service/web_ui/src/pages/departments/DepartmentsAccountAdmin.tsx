import {
  Search,
  CheckCircle,
  Eye,
  ShieldCheck,
  Building2,
  Users,
  Server,
  LockKeyhole,
  Cog,
  FileText,
  Bot,
  Plus,
  Edit3,
  Archive,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";

/**
 * Port of departments-account_admin.html (bob).
 * Middle: 5 departments grouped ACTIVE / GUEST / SPECIAL.
 * Workzone: Ядро DBOS overview — stats, grants matrix, members preview, recent activity.
 */
interface DeptRow {
  name: string;
  desc: string;
  users: string;
  servers: string;
  creds: string;
  active?: boolean;
  iconKind?: "accent" | "dim" | "warn";
  iconType?: "building" | "shield";
}

const ACTIVE: DeptRow[] = [
  {
    name: "Ядро DBOS",
    desc: "платформа: ОС, ядро, БД",
    users: "12",
    servers: "30",
    creds: "12",
    active: true,
    iconKind: "accent",
  },
  { name: "ДТКК", desc: "тулзы: jira / wiki / build", users: "15", servers: "25", creds: "18" },
  { name: "Инфра", desc: "сеть, мониторинг, edge", users: "8", servers: "20", creds: "9" },
];

const GUEST: DeptRow[] = [
  {
    name: "Гость",
    desc: "внешние подрядчики, read-only",
    users: "7",
    servers: "0",
    creds: "0",
  },
];

const SPECIAL: DeptRow[] = [
  {
    name: "Платформенные",
    desc: "учётки без департамента (account_admin, logging_admin, logging_reader)",
    users: "5",
    servers: "—",
    creds: "—",
    iconType: "shield",
    iconKind: "warn",
  },
];

interface GrantRow {
  icon: React.ReactNode;
  service: string;
  read: "granted" | "scoped" | "none";
  write: "granted" | "scoped" | "none";
  admin: "granted" | "scoped" | "none";
  notes: string;
}

const GRANTS: GrantRow[] = [
  {
    icon: <ShieldCheck className="w-3.5 h-3.5 inline" />,
    service: "auth_service",
    read: "granted",
    write: "granted",
    admin: "none",
    notes: "dep_admin может назначать роли внутри depta",
  },
  {
    icon: <Server className="w-3.5 h-3.5 inline" />,
    service: "server_service",
    read: "granted",
    write: "granted",
    admin: "scoped",
    notes: "только серверы депа",
  },
  {
    icon: <Cog className="w-3.5 h-3.5 inline" />,
    service: "server_worker",
    read: "granted",
    write: "granted",
    admin: "none",
    notes: "re-run / cancel — только свои tasks",
  },
  {
    icon: <LockKeyhole className="w-3.5 h-3.5 inline" />,
    service: "secret_service",
    read: "granted",
    write: "granted",
    admin: "scoped",
    notes: "creds депа; cross-dep — через account_admin",
  },
  {
    icon: <FileText className="w-3.5 h-3.5 inline" />,
    service: "logging_service",
    read: "granted",
    write: "none",
    admin: "none",
    notes: "read-only; запись — system-only",
  },
  {
    icon: <Bot className="w-3.5 h-3.5 inline" />,
    service: "config_service",
    read: "granted",
    write: "scoped",
    admin: "none",
    notes: "токены сервисов депа",
  },
];

function grantLabel(g: "granted" | "scoped" | "none") {
  if (g === "granted") return "granted";
  if (g === "scoped") return "scoped";
  return "—";
}

function grantClass(g: "granted" | "scoped" | "none") {
  const base = "inline-block text-[10px] py-0.5 px-1.5 rounded border";
  if (g === "granted")
    return `${base} text-ok border-ok bg-ok/5`;
  if (g === "scoped")
    return `${base} text-warn border-warn bg-warn/5`;
  return `${base} border-token text-dim`;
}

export function DepartmentsAccountAdmin() {
  return (
    <Shell breadcrumb="auth_service / departments">
      <aside className="w-[340px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder="Поиск по department'ам..."
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim">
            <span>Группировка:</span>
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>by status</option>
              <option>by created</option>
            </select>
            <span className="ml-auto">5 / 5</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <div className="group-header flex items-center gap-2">
            <CheckCircle className="w-3 h-3 text-ok" /> ACTIVE · 3
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {ACTIVE.map((d) => (
              <DeptItem key={d.name} d={d} />
            ))}
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <Eye className="w-3 h-3" /> GUEST · 1
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {GUEST.map((d) => (
              <DeptItem key={d.name} d={d} />
            ))}
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <ShieldCheck className="w-3 h-3 text-warn" /> SPECIAL · 1
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {SPECIAL.map((d) => (
              <DeptItem key={d.name} d={d} />
            ))}
          </div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <Plus className="w-4 h-4" /> Создать department
          </button>
        </div>
      </aside>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="border-b border-token p-5 flex items-start gap-4">
          <div className="w-14 h-14 rounded bg-accent flex items-center justify-center">
            <Building2 className="w-9 h-9" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate">Ядро DBOS</h1>
              <span className="badge badge-ok">ACTIVE</span>
              <span className="text-xs text-dim mono">dept_kernel_dbos</span>
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span>
                <Users className="w-3 h-3 inline" /> <b>12</b> users
              </span>
              <span>·</span>
              <span>
                <Server className="w-3 h-3 inline" /> <b>30</b> servers
              </span>
              <span>·</span>
              <span>
                <LockKeyhole className="w-3 h-3 inline" /> <b>12</b> credentials
              </span>
              <span>·</span>
              <span>
                dep_admin: <span className="text-accent">alice</span>
              </span>
              <span>·</span>
              <span>
                created: <b>2024-11-15</b>
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button className="btn flex items-center gap-1">
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button className="btn btn-danger flex items-center gap-1">
              <Archive className="w-4 h-4" /> Archive
            </button>
          </div>
        </div>

        <div className="border-b border-token px-5 flex gap-1">
          {["Overview", "Members", "Service grants", "Audit"].map((tab, i) => (
            <button
              key={tab}
              className={`px-3 py-2 text-sm border-b-2 -mb-px ${
                i === 0
                  ? "border-accent text-accent"
                  : "border-transparent text-dim"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
          {/* Stats */}
          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-3">Stats</div>
            <div className="grid grid-cols-4 gap-3">
              <StatTile
                icon={<Users className="w-3.5 h-3.5" />}
                label="Users"
                num="12"
                hint="из них 1 dep_admin, 2 dep_user_admin"
              />
              <StatTile
                icon={<Server className="w-3.5 h-3.5" />}
                label="Servers"
                num="30"
                hint="27 up · 2 down · 1 maint"
              />
              <StatTile
                icon={<LockKeyhole className="w-3.5 h-3.5" />}
                label="Credentials"
                num="12"
                hint="root / ansible / monitor / pg"
              />
              <StatTile
                icon={<Cog className="w-3.5 h-3.5" />}
                label="Tasks (24ч)"
                num="147"
                hint="142 ok · 4 fail · 1 running"
              />
            </div>
          </div>

          {/* Service grants matrix */}
          <div className="surface border border-token rounded-lg p-4 col-span-2">
            <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4" /> Service-grants matrix
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">Сервис</th>
                  <th className="pb-2 pr-3 text-center">read</th>
                  <th className="pb-2 pr-3 text-center">write</th>
                  <th className="pb-2 pr-3 text-center">admin</th>
                  <th className="pb-2">Заметки</th>
                </tr>
              </thead>
              <tbody>
                {GRANTS.map((g) => (
                  <tr key={g.service} className="border-t border-token">
                    <td className="py-2">
                      {g.icon} {g.service}
                    </td>
                    <td className="text-center">
                      <span className={grantClass(g.read)}>{grantLabel(g.read)}</span>
                    </td>
                    <td className="text-center">
                      <span className={grantClass(g.write)}>{grantLabel(g.write)}</span>
                    </td>
                    <td className="text-center">
                      <span className={grantClass(g.admin)}>{grantLabel(g.admin)}</span>
                    </td>
                    <td className="text-xs text-dim">{g.notes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Members preview */}
          <div className="surface border border-token rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs uppercase tracking-wider text-dim flex items-center gap-2">
                <Users className="w-4 h-4" /> Members (5 из 12)
              </div>
              <span className="text-xs text-accent cursor-pointer">Все →</span>
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">User</th>
                  <th className="pb-2 pr-3">Роль</th>
                  <th className="pb-2">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { name: "alice", role: "dep_admin", badge: "badge-warn", seen: "2 мин" },
                  { name: "carol", role: "dep_user_admin", badge: "", seen: "15 мин" },
                  { name: "dave", role: "dep_user_admin", badge: "", seen: "1 ч" },
                  { name: "igor", role: "dep_user", badge: "", seen: "3 ч" },
                  { name: "pavel", role: "dep_user", badge: "", seen: "1 дн" },
                ].map((r) => (
                  <tr key={r.name} className="border-t border-token">
                    <td className="py-2 text-accent">{r.name}</td>
                    <td>
                      <span className={`badge ${r.badge}`}>{r.role}</span>
                    </td>
                    <td className="text-dim text-xs">{r.seen}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Recent activity */}
          <div className="surface border border-token rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs uppercase tracking-wider text-dim flex items-center gap-2">
                <FileText className="w-4 h-4" /> Recent activity
              </div>
              <span className="text-xs text-dim">audit недоступен</span>
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">Время</th>
                  <th className="pb-2 pr-3">Actor</th>
                  <th className="pb-2">Событие</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { t: "15:42", a: "alice", e: "user.role_grant — igor → dep_user" },
                  { t: "14:18", a: "bob", e: "dept.update — описание изменено" },
                  { t: "12:55", a: "alice", e: "server.move — srv-node-12 in" },
                  { t: "2 дн", a: "bob", e: "dept.create — Ядро DBOS" },
                  { t: "7 дн", a: "system", e: "grants.review — passed" },
                ].map((r) => (
                  <tr key={r.t + r.a} className="border-t border-token">
                    <td className="py-2 mono text-xs">{r.t}</td>
                    <td className="text-xs">{r.a}</td>
                    <td className="text-xs">{r.e}</td>
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

function DeptItem({ d }: { d: DeptRow }) {
  const iconColor =
    d.iconKind === "accent"
      ? "text-accent"
      : d.iconKind === "warn"
      ? "text-warn"
      : "text-dim";
  const Icon = d.iconType === "shield" ? ShieldCheck : Building2;
  return (
    <div className={`cred-row ${d.active ? "active" : ""}`}>
      <div className="flex items-start gap-2">
        <Icon className={`w-5 h-5 mt-0.5 ${iconColor}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium">{d.name}</div>
          <div className="text-[11px] text-dim mt-0.5">{d.desc}</div>
          <div className="text-[11px] text-dim mt-1 flex items-center gap-3">
            <span>
              <Users className="w-3 h-3 inline" /> {d.users}
            </span>
            <span>
              <Server className="w-3 h-3 inline" /> {d.servers}
            </span>
            <span>
              <LockKeyhole className="w-3 h-3 inline" /> {d.creds}
            </span>
          </div>
          <span className="text-[11px] text-accent mt-1 inline-block cursor-pointer">
            → admin panel
          </span>
        </div>
      </div>
    </div>
  );
}

function StatTile({
  icon,
  label,
  num,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  num: string;
  hint: string;
}) {
  return (
    <div className="surface-2 border border-token rounded p-3">
      <div className="text-xs text-dim flex items-center gap-1.5">
        {icon} {label}
      </div>
      <div className="text-2xl font-semibold mt-1 leading-tight">{num}</div>
      <div className="text-[11px] text-dim mt-1">{hint}</div>
    </div>
  );
}
