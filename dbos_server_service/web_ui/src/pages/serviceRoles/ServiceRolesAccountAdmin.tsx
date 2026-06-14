import {
  Search,
  Filter,
  Server,
  LockKeyhole,
  FileText,
  Cog,
  ShieldCheck,
  KeyRound,
  UsersRound,
  Edit3,
  Trash2,
  User,
  Clock,
  Bot,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { ServiceRolesLivePanel } from "./ServiceRolesLivePanel";
import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";

/**
 * Account-admin service-roles screen (static; not yet wired to live data).
 * Middle: 20 roles grouped by service. Workzone: devops (custom · Ядро DBOS).
 */
interface RoleRow {
  name: string;
  desc: string;
  kind: "system" | "custom";
  active?: boolean;
}

interface RoleGroup {
  icon: React.ReactNode;
  label: string;
  rows: RoleRow[];
}

const GROUPS: RoleGroup[] = [
  {
    icon: <Server className="w-3 h-3" />,
    label: "server_service · 5",
    rows: [
      { name: "admin", desc: "полный CRUD", kind: "system" },
      { name: "operator", desc: "create/update servers", kind: "system" },
      { name: "reader", desc: "read-only", kind: "system" },
      { name: "guest", desc: "список ОС/инвентарь", kind: "system" },
      { name: "devops", desc: "Ядро DBOS", kind: "custom" },
    ],
  },
  {
    icon: <LockKeyhole className="w-3 h-3" />,
    label: "secret_service · 6",
    rows: [
      { name: "admin", desc: "полный CRUD", kind: "system" },
      { name: "operator", desc: "create/rotate", kind: "system" },
      { name: "reader", desc: "read-only", kind: "system" },
      { name: "guest", desc: "metadata only", kind: "system" },
      { name: "devops", desc: "Ядро DBOS", kind: "custom", active: true },
      { name: "read-only-credentials", desc: "ДТКК", kind: "custom" },
    ],
  },
  {
    icon: <FileText className="w-3 h-3" />,
    label: "loging_service · 5",
    rows: [
      { name: "admin", desc: "events + rules + retention", kind: "system" },
      { name: "operator", desc: "events + rules", kind: "system" },
      { name: "reader", desc: "events read-only", kind: "system" },
      { name: "guest", desc: "summary stats", kind: "system" },
      { name: "auditor", desc: "Ядро DBOS", kind: "custom" },
    ],
  },
  {
    icon: <Cog className="w-3 h-3" />,
    label: "server_worker · 4",
    rows: [
      { name: "admin", desc: "submit + cancel + tune", kind: "system" },
      { name: "operator", desc: "submit jobs", kind: "system" },
      { name: "reader", desc: "jobs status", kind: "system" },
      { name: "guest", desc: "queue size only", kind: "system" },
    ],
  },
];

export function ServiceRolesAccountAdmin() {
  const { persona } = usePersona();
  const deptId = personaDeptId(persona) ?? "core";
  return (
    <Shell breadcrumb="auth_service / service-roles">
      <aside className="w-[360px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder="Поиск ролей..."
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
            <Filter className="w-3 h-3" />
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>все сервисы</option>
              <option>server_service</option>
              <option>secret_service</option>
              <option>loging_service</option>
              <option>server_worker</option>
            </select>
            <select className="surface-2 border border-token rounded px-2 py-0.5">
              <option>all</option>
              <option>system</option>
              <option>custom</option>
            </select>
            <span className="ml-auto">20 шт</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {GROUPS.map((g, gi) => (
            <div key={g.label}>
              <div
                className={`group-header flex items-center gap-2 ${gi > 0 ? "mt-3" : ""}`}
              >
                {g.icon} {g.label}
              </div>
              <div className="px-2 flex flex-col gap-0.5">
                {g.rows.map((row, i) => (
                  <RoleItem key={`${g.label}-${row.name}-${i}`} row={row} />
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <ShieldCheck className="w-4 h-4" /> Создать custom-роль
          </button>
          <div className="text-[10px] text-dim mt-1 text-center">
            system-роли иммутабельны
          </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
        <div className="flex-1 min-h-0 flex">
          <RoleDetail
            name="devops"
            service="secret_service"
            scope="Ядро DBOS"
            roleId="role_8a3c9d2e1f"
            grantsStat="2 grants ✓ · 1 deny ✗"
            membersStat="3 users · 2 bots"
            createdBy="alice"
            createdAt="2026-03-04"
            grants={[
              { action: "read_credential", allowed: true, notes: "любой credential в Ядро DBOS" },
              { action: "write_credential", allowed: true, notes: "создание + ротация" },
              { action: "revoke_credential", allowed: false, notes: "только service_roles.secret = admin" },
              { action: "read_audit", allowed: true, notes: "только свои операции" },
              { action: "manage_provision_creds", allowed: false, notes: "stash через Redis off-limits" },
            ]}
            members={[
              { type: "user", name: "dave", dept: "Ядро DBOS", since: "2026-03-05" },
              { type: "user", name: "igor", dept: "Ядро DBOS", since: "2026-03-05" },
              { type: "user", name: "grace", dept: "Ядро DBOS", since: "2026-04-12" },
              { type: "bot", name: "dbos_bot_secret_sweeper", dept: "Ядро DBOS", since: "2026-03-06" },
              { type: "bot", name: "dbos_bot_rotation_runner", dept: "Ядро DBOS", since: "2026-04-01" },
            ]}
          />
        </div>
        <div className="border-t border-token shrink-0 overflow-y-auto max-h-[50%]">
          <ServiceRolesLivePanel
            departmentId={deptId}
            scopeNote="account_admin · scope любой отдел; переключите selector"
          />
        </div>
      </div>
    </Shell>
  );
}

function RoleItem({ row }: { row: RoleRow }) {
  return (
    <div className={`cred-row ${row.active ? "active" : ""}`}>
      <div className="flex items-center gap-2">
        <ShieldCheck
          className={`w-4 h-4 ${row.kind === "custom" ? "text-accent" : "text-dim"}`}
        />
        <div className="flex-1 min-w-0">
          <div className="text-sm mono">{row.name}</div>
          <div className="text-[11px] text-dim">{row.desc}</div>
        </div>
        <span className={`badge ${row.kind === "custom" ? "badge-accent" : ""}`}>
          {row.kind}
        </span>
      </div>
    </div>
  );
}

interface GrantRow {
  action: string;
  allowed: boolean;
  notes: string;
}
interface MemberRow {
  type: "user" | "bot";
  name: string;
  dept: string;
  since: string;
}

interface RoleDetailProps {
  name: string;
  service: string;
  scope: string;
  roleId: string;
  grantsStat: string;
  membersStat: string;
  createdBy: string;
  createdAt: string;
  grants: GrantRow[];
  members: MemberRow[];
}

export function RoleDetail(props: RoleDetailProps) {
  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded surface-2 border border-token flex items-center justify-center">
          <ShieldCheck className="w-6 h-6 text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">{props.name}</h1>
            <span className="badge badge-accent">custom</span>
            <span className="badge">{props.service}</span>
            <span className="badge">scope: {props.scope}</span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono">{props.roleId}</span>
            <span>·</span>
            <span>{props.grantsStat}</span>
            <span>·</span>
            <span>{props.membersStat}</span>
          </div>
          <div className="text-xs text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="flex items-center gap-1">
              <User className="w-3 h-3" /> создал{" "}
              <span className="mono">{props.createdBy}</span>
            </span>
            <span>·</span>
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" /> {props.createdAt}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <button className="btn flex items-center gap-1">
            <Edit3 className="w-4 h-4" /> Edit grants
          </button>
          <button className="btn btn-danger flex items-center gap-1">
            <Trash2 className="w-4 h-4" /> Delete
          </button>
        </div>
      </div>

      <div className="border-b border-token px-5 flex gap-1 flex-wrap">
        {["Overview", "Grants", "Members", "Audit"].map((tab, i) => (
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
            <ShieldCheck className="w-4 h-4" /> Definition
          </div>
          <div className="grid grid-cols-2 gap-x-6 text-sm">
            <div>
              <StatRow k="name" v={<span className="mono">{props.name}</span>} />
              <StatRow k="service" v={<span className="mono">{props.service}</span>} />
              <StatRow k="scope_dept" v={props.scope} />
            </div>
            <div>
              <StatRow k="system" v="false" />
              <StatRow k="mutable" v="true" />
              <StatRow k="created_by" v={<span className="mono">{props.createdBy}</span>} />
            </div>
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <KeyRound className="w-4 h-4" /> Grants
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">action</th>
                <th className="pb-2 pr-3">allowed</th>
                <th className="pb-2">notes</th>
              </tr>
            </thead>
            <tbody>
              {props.grants.map((g) => (
                <tr key={g.action} className="border-t border-token">
                  <td className="py-2 mono">{g.action}</td>
                  <td>
                    {g.allowed ? (
                      <span className="text-ok">✓</span>
                    ) : (
                      <span className="text-danger">✗</span>
                    )}
                  </td>
                  <td className="text-dim text-xs">{g.notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
            <UsersRound className="w-4 h-4" /> Members
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">type</th>
                <th className="pb-2 pr-3">name</th>
                <th className="pb-2 pr-3">dept</th>
                <th className="pb-2 pr-3">since</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {props.members.map((m) => (
                <tr key={m.name} className="border-t border-token">
                  <td className="py-2">
                    {m.type === "user" ? (
                      <User className="w-4 h-4 inline" />
                    ) : (
                      <Bot className="w-4 h-4 inline" />
                    )}
                  </td>
                  <td className="mono">{m.name}</td>
                  <td>{m.dept}</td>
                  <td className="text-dim text-xs">{m.since}</td>
                  <td>
                    <button className="btn btn-danger text-xs">revoke</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
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
