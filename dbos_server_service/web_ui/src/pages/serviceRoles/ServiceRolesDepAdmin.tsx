import { Search, Filter, ShieldCheck } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { RoleDetail } from "./ServiceRolesAccountAdmin";
import { ServiceRolesLivePanel } from "./ServiceRolesLivePanel";
import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";

/**
 * Dep-admin service-roles screen (static; not yet wired to live data).
 * Middle: 8 roles — system × 4 + custom × 4 (Ядро DBOS).
 * Workzone: auditor (loging_service · Ядро DBOS).
 */
interface Row {
  name: string;
  desc: string;
  kind: "system" | "custom";
  active?: boolean;
}
const SYSTEM: Row[] = [
  { name: "server_service · reader", desc: "read-only", kind: "system" },
  { name: "server_service · operator", desc: "CRUD по dept", kind: "system" },
  { name: "secret_service · reader", desc: "read-only", kind: "system" },
  { name: "loging_service · reader", desc: "events read-only", kind: "system" },
];
const CUSTOM: Row[] = [
  { name: "server_service · devops", desc: "CRUD + reboot", kind: "custom" },
  { name: "secret_service · devops", desc: "read + rotate", kind: "custom" },
  { name: "loging_service · auditor", desc: "read + export", kind: "custom", active: true },
  { name: "server_worker · ci_runner", desc: "submit + read", kind: "custom" },
];

export function ServiceRolesDepAdmin() {
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
            <span className="ml-auto">8 шт · Ядро DBOS</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          <div className="group-header flex items-center gap-2">
            <ShieldCheck className="w-3 h-3" /> System · 4
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {SYSTEM.map((r) => (
              <RoleItem key={r.name} row={r} />
            ))}
          </div>

          <div className="group-header flex items-center gap-2 mt-3">
            <ShieldCheck className="w-3 h-3 text-accent" /> Custom (Ядро DBOS) · 4
          </div>
          <div className="px-2 flex flex-col gap-0.5">
            {CUSTOM.map((r) => (
              <RoleItem key={r.name} row={r} />
            ))}
          </div>
        </div>

        <div className="border-t border-token p-3">
          <button className="btn btn-primary w-full flex items-center justify-center gap-2">
            <ShieldCheck className="w-4 h-4" /> Создать custom-роль
          </button>
          <div className="text-[10px] text-dim mt-1 text-center">
            scope ограничен Ядро DBOS
          </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
        <div className="flex-1 min-h-0 flex">
          <RoleDetail
            name="auditor"
            service="loging_service"
            scope="Ядро DBOS"
            roleId="role_4d9a1c8e3b"
            grantsStat="3 grants ✓ · 1 deny ✗"
            membersStat="2 users · 1 bot"
            createdBy="alice"
            createdAt="2026-04-18"
            grants={[
              { action: "read_events", allowed: true, notes: "только Ядро DBOS" },
              { action: "export_events", allowed: true, notes: "CSV/JSON dump" },
              { action: "read_audit_chain", allowed: true, notes: "hash-chain integrity" },
              { action: "manage_rules", allowed: false, notes: "только loging_admin" },
              { action: "manage_retention", allowed: false, notes: "только loging_admin" },
            ]}
            members={[
              { type: "user", name: "grace", dept: "Ядро DBOS", since: "2026-04-18" },
              { type: "user", name: "henry", dept: "Ядро DBOS", since: "2026-05-02" },
              { type: "bot", name: "dbos_bot_audit_export", dept: "Ядро DBOS", since: "2026-04-18" },
            ]}
          />
        </div>
        <div className="border-t border-token shrink-0 overflow-y-auto max-h-[50%]">
          <ServiceRolesLivePanel
            departmentId={deptId}
            scopeNote="dep_admin · scope ограничен своим отделом"
          />
        </div>
      </div>
    </Shell>
  );
}

function RoleItem({ row }: { row: Row }) {
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
