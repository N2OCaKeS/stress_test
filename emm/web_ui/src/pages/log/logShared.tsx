import { useState } from "react";
import { ChevronDown, Filter, User } from "lucide-react";
import { Checkbox } from "@/components/ui/Checkbox";

export type Sev = "CRITICAL" | "ERROR" | "WARNING" | "INFO";

export interface EventRow {
  id: string;
  time: string;
  action: string;
  meta: string;
  severity: Sev;
  label: string;
}

export const EVENT_ROWS: EventRow[] = [
  { id: "ev-001", time: "15:42:11", action: "credential.read", meta: "alice → alice-personal-vault", severity: "INFO", label: "INFO" },
  { id: "ev-002", time: "15:41:55", action: "secret.master_key_rotated", meta: "carol → master_key_v3", severity: "CRITICAL", label: "CRIT" },
  { id: "ev-003", time: "15:40:32", action: "user.login_failed", meta: "unknown@10.20.30.4 · 3rd attempt", severity: "WARNING", label: "WARN" },
  { id: "ev-004", time: "15:38:01", action: "server.power_action", meta: "ci_runner → srv-rack-A-07 · cycle", severity: "INFO", label: "INFO" },
  { id: "ev-005", time: "15:37:18", action: "task.failed", meta: "worker-2 → task_8a1c · timeout", severity: "ERROR", label: "ERR" },
  { id: "ev-006", time: "15:36:44", action: "credential.revoke", meta: "alice → vault-bootstrap-root", severity: "WARNING", label: "WARN" },
  { id: "ev-007", time: "15:35:12", action: "server.ipmi_unreachable", meta: "monitor → srv-rack-B-12", severity: "ERROR", label: "ERR" },
  { id: "ev-008", time: "15:33:09", action: "user.password_change", meta: "dave → self", severity: "INFO", label: "INFO" },
  { id: "ev-009", time: "15:30:55", action: "credential.read", meta: "bob → prod-postgres-master", severity: "INFO", label: "INFO" },
  { id: "ev-010", time: "15:28:41", action: "task.completed", meta: "ci_runner → task_7f23", severity: "INFO", label: "INFO" },
  { id: "ev-011", time: "15:25:02", action: "role_acl_updated", meta: "alice → dept:Ядро DBOS", severity: "WARNING", label: "WARN" },
  { id: "ev-012", time: "15:22:19", action: "user.sessions_revoked_on_block", meta: "alice → igor · 4 sessions", severity: "CRITICAL", label: "CRIT" },
  { id: "ev-013", time: "15:18:46", action: "credential.transfer", meta: "alice → bob · github-deploy-token", severity: "WARNING", label: "WARN" },
  { id: "ev-014", time: "15:14:33", action: "user.login_failed", meta: "igor@10.20.30.4 · blocked", severity: "ERROR", label: "ERR" },
  { id: "ev-015", time: "15:11:08", action: "task.started", meta: "ci_runner → task_7f23", severity: "INFO", label: "INFO" },
  { id: "ev-016", time: "15:07:51", action: "task.retry", meta: "worker-1 → task_8a1c · attempt 2/3", severity: "WARNING", label: "WARN" },
  { id: "ev-017", time: "15:02:14", action: "service.rotation_started", meta: "secret_service → master_key", severity: "CRITICAL", label: "CRIT" },
  { id: "ev-018", time: "14:58:39", action: "credential.create", meta: "bob → bmc-rack-C-ipmi", severity: "INFO", label: "INFO" },
  { id: "ev-019", time: "14:54:17", action: "dep_grant_added", meta: "alice → grant:Разработка.read", severity: "WARNING", label: "WARN" },
  { id: "ev-020", time: "14:50:02", action: "credential.read", meta: "ci_runner → github-deploy-token", severity: "INFO", label: "INFO" },
  { id: "ev-021", time: "14:46:25", action: "user.login_success", meta: "dave@10.20.30.5 · web", severity: "INFO", label: "INFO" },
  { id: "ev-022", time: "14:42:48", action: "server.account_create", meta: "bob → srv-rack-A-08 · admin", severity: "WARNING", label: "WARN" },
  { id: "ev-023", time: "14:39:11", action: "task.failed", meta: "worker-3 → task_4c2b · 500", severity: "ERROR", label: "ERR" },
  { id: "ev-024", time: "14:36:01", action: "credential.read", meta: "alice → grafana-admin · 429 rate-limit", severity: "WARNING", label: "WARN" },
  { id: "ev-025", time: "14:30:48", action: "user.login_failed", meta: "unknown@198.51.100.7 · invalid_creds", severity: "ERROR", label: "ERR" },
  { id: "ev-026", time: "14:24:33", action: "service.started", meta: "server_worker boot · v1.4.2", severity: "INFO", label: "INFO" },
  { id: "ev-027", time: "14:18:11", action: "credential.read", meta: "bob → bmc-rack-A-ipmi", severity: "INFO", label: "INFO" },
  { id: "ev-028", time: "14:11:55", action: "server.power_action", meta: "bob → srv-rack-A-09 · off", severity: "WARNING", label: "WARN" },
  { id: "ev-029", time: "14:05:22", action: "task.completed", meta: "ci_runner → task_3a91", severity: "INFO", label: "INFO" },
  { id: "ev-030", time: "14:01:09", action: "user.login_failed", meta: "igor@10.20.30.4 · invalid_creds", severity: "ERROR", label: "ERR" },
];

export interface FacetItem {
  label: string;
  count: number;
  sevTag?: Sev;
}

export const SEVERITY_FACETS: FacetItem[] = [
  { label: "CRITICAL", count: 5, sevTag: "CRITICAL" },
  { label: "ERROR", count: 8, sevTag: "ERROR" },
  { label: "WARNING", count: 10, sevTag: "WARNING" },
  { label: "INFO", count: 7, sevTag: "INFO" },
];

export const ACTION_FACETS: FacetItem[] = [
  { label: "credential.read", count: 412 },
  { label: "user.login_success", count: 203 },
  { label: "task.completed", count: 187 },
  { label: "server.power_action", count: 98 },
  { label: "user.login_failed", count: 42 },
  { label: "task.failed", count: 31 },
  { label: "credential.create", count: 14 },
  { label: "secret.master_key_rotated", count: 2 },
];

export const ACTOR_FACETS: FacetItem[] = [
  { label: "alice", count: 154 },
  { label: "bob", count: 98 },
  { label: "ci_runner", count: 82 },
  { label: "carol", count: 41 },
  { label: "bootstrap", count: 18 },
];

export const TARGET_FACETS: FacetItem[] = [
  { label: "user", count: 245 },
  { label: "credential", count: 426 },
  { label: "server", count: 98 },
  { label: "dept", count: 12 },
  { label: "task", count: 218 },
];

export const STATUS_FACETS: FacetItem[] = [
  { label: "success", count: 927 },
  { label: "failure", count: 72 },
];

interface FacetsProps {
  title: string;
  hint?: string;
  items: FacetItem[];
  severityFacets?: boolean;
  mono?: boolean;
  icon?: "filter" | "user";
}

export function Facets({
  title,
  hint,
  items,
  severityFacets,
  mono,
  icon = "filter",
}: FacetsProps) {
  const [open, setOpen] = useState(true);
  const Icon = icon === "user" ? User : Filter;
  return (
    <div>
      <button
        onClick={() => setOpen((x) => !x)}
        className="facet-header w-full text-left"
      >
        <div className="flex items-center gap-2">
          <Icon className="w-3.5 h-3.5" />
          <b>{title}</b>
          {hint && <span className="text-dim">{hint}</span>}
        </div>
        <ChevronDown
          className={`w-3.5 h-3.5 transition-transform ${open ? "" : "-rotate-90"}`}
        />
      </button>
      {open && (
        <div className="facet-body">
          {items.map((it) => (
            <label key={it.label} className="facet-item">
              <Checkbox
                defaultChecked={severityFacets}
              />
              {severityFacets && it.sevTag ? (
                <span className={`sev sev-${it.sevTag}`}>{it.label}</span>
              ) : mono ? (
                <span className="mono">{it.label}</span>
              ) : (
                <span>{it.label}</span>
              )}
              <span className="count">{it.count}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
