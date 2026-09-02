/* loging_service mock: 200 audit events + alert rules + retention policies. */

export type AuditSeverity = "info" | "warning" | "critical";

export interface MockRule {
  id: string;
  expr: string;
  severity: "WARNING" | "ERROR" | "CRITICAL";
  enabled: boolean;
  updated: string;
}

export interface MockRetention {
  id: string;
  scope: string;
  days: number;
  lastSweep: string;
  nextSweep: string;
  size: string;
}

// Alert rules — DSL-style expressions evaluated by loging_service every 60s.
// Used by /admin/services (ServicesLogingRules inline editor). The richer
// classifier-style rules on /log/rules (LogRules) live there inline because
// they carry different fields (pattern grouping, badgeKind etc).
export const LOG_RULES: MockRule[] = [
  { id: "r-cred-read-100h", expr: "credential.read > 100/h", severity: "WARNING", enabled: true, updated: "2д назад" },
  { id: "r-login-failed-5", expr: "login.failed × 5", severity: "CRITICAL", enabled: true, updated: "5д назад" },
  { id: "r-tls-expiry-14d", expr: "tls.expiry < 14d", severity: "WARNING", enabled: true, updated: "1ч назад" },
  { id: "r-dlq-age-24h", expr: "dlq.age > 24h", severity: "ERROR", enabled: true, updated: "3д назад" },
  { id: "r-server-down-5m", expr: "server.down > 5m", severity: "CRITICAL", enabled: true, updated: "12д назад" },
  { id: "r-rotation-late", expr: "rotation.due AND not rotated", severity: "WARNING", enabled: false, updated: "1ч назад" },
];

// Retention policies — per-scope TTL for audit/metrics tables.
export const LOG_RETENTION: MockRetention[] = [
  { id: "audit", scope: "audit · все события", days: 90, lastSweep: "04.06 03:00", nextSweep: "11.06 03:00", size: "2.7 GB" },
  { id: "audit-crit", scope: "audit · severity=CRITICAL", days: 365, lastSweep: "04.06 03:00", nextSweep: "11.06 03:00", size: "412 MB" },
  { id: "metrics", scope: "metrics · raw", days: 30, lastSweep: "04.06 03:00", nextSweep: "11.06 03:00", size: "8.1 GB" },
  { id: "metrics-1h", scope: "metrics · 1h aggregated", days: 365, lastSweep: "04.06 03:00", nextSweep: "11.06 03:00", size: "1.2 GB" },
];

export interface MockAuditEvent {
  id: string;
  ts: string;
  actor: string;
  action: string;
  target: string;
  service: string;
  result: "success" | "denied" | "error";
  severity: AuditSeverity;
  request_id: string;
}

const ACTIONS = [
  "user.create", "user.update", "user.delete", "user.login", "user.logout",
  "credential.reveal", "credential.rotate", "credential.create",
  "server.reboot", "server.power_on", "server.power_off",
  "role.grant", "role.revoke",
  "task.enqueue", "task.complete", "task.fail",
  "audit.export", "rule.update", "retention.apply",
];
const SERVICES = ["auth", "secret", "server", "worker", "logging"];
const ACTORS = ["alice", "bob", "carol", "dave", "igor", "pavel", "worker_bot", "cron"];

function genEvents(): MockAuditEvent[] {
  const out: MockAuditEvent[] = [];
  for (let i = 0; i < 200; i++) {
    const action = ACTIONS[i % ACTIONS.length];
    const result = i % 19 === 0 ? "error" : i % 23 === 0 ? "denied" : "success";
    let severity: AuditSeverity = "info";
    if (result === "error") severity = "critical";
    else if (result === "denied" || i % 17 === 0) severity = "warning";
    out.push({
      id: `ev-${String(i + 1).padStart(4, "0")}`,
      ts: `2026-06-${String(10 - Math.floor(i / 50)).padStart(2, "0")}T${String((i * 11) % 24).padStart(2, "0")}:${String((i * 17) % 60).padStart(2, "0")}:00Z`,
      actor: ACTORS[i % ACTORS.length],
      action,
      target: `obj-${String((i * 3) % 90 + 1).padStart(3, "0")}`,
      service: SERVICES[i % SERVICES.length],
      result,
      severity,
      request_id: `req_${(0x1000 + i).toString(16)}`,
    });
  }
  return out;
}

export const AUDIT_EVENTS: MockAuditEvent[] = genEvents();
