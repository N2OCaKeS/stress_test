/* Cluster-level mocks for the admin section: health, TLS, rotations,
   backups, migrations, audit overview, global config, workers, cron, DLQ. */

export interface ClusterPod {
  svc: string;
  ratio: string;
  iconName: "lock" | "server" | "key" | "doc" | "cog";
}

export const CLUSTER_PODS: ClusterPod[] = [
  { svc: "auth_service", ratio: "2/2", iconName: "lock" },
  { svc: "server_service", ratio: "3/3", iconName: "server" },
  { svc: "secret_service", ratio: "2/2", iconName: "key" },
  { svc: "loging_service", ratio: "2/2", iconName: "doc" },
  { svc: "server_worker", ratio: "4/4", iconName: "cog" },
];

export const ROTATIONS = [
  { name: "server-master", next: "12.06 02:00", last: "05.06" },
  { name: "secret-master", next: "12.06 03:00", last: "05.06" },
  { name: "redis-stash-master", next: "12.06 04:00", last: "05.06" },
  { name: "db-passwords", next: "15.06", last: "15.05" },
  { name: "redis-password", next: "15.06", last: "15.05" },
  { name: "s2s-keys", next: "18.06", last: "18.05" },
];

export const BACKUPS = [
  { name: "pg-backup", meta: "daily · 2h ago · 412 MB", badge: "ok" },
  { name: "master-keys-backup", meta: "daily · 2h ago · 64 KB", badge: "ok" },
  { name: "secret-full-backup", meta: "weekly · 4д назад · 18 MB", badge: "ok" },
  { name: "pg-restore-drill", meta: "monthly · 11д назад · pass", badge: "pass" },
];

export interface GlobalConfigItem {
  key: string;
  value: string;
  note: string;
}

export const GLOBAL_CONFIG_ITEMS: GlobalConfigItem[] = [
  { key: "TLS_MIN_VERSION", value: "1.3", note: "minimum cluster-wide TLS" },
  { key: "AUDIT_RETENTION_DAYS", value: "90", note: "loging sweep period" },
  { key: "ROTATION_MASTER_PERIOD", value: "7d", note: "server / secret / redis-stash master" },
  { key: "MIGRATION_AUTO_FINALIZE", value: "6mo", note: "форсированное дошифрование legacy" },
  { key: "NETWORK_POLICY", value: "strict", note: "default-deny + namespaced east-west" },
  { key: "DEPT_ISOLATION", value: "on", note: "cross-dept reads запрещены" },
];

export interface ClusterConfigAuditEntry {
  /** ISO-8601 (UTC); рендерится через formatMsk*. */
  ts: string;
  key: string;
  old_value: string;
  new_value: string;
  applied_by: string;
}

// shared in-memory audit log for cluster.config live patches.
// real impl will read from loging_service; mock keeps last entries in process.
export const CLUSTER_CONFIG_AUDIT: ClusterConfigAuditEntry[] = [];

export interface WorkerPod {
  id: string;
  host: string;
  status: "running" | "idle" | "draining";
  tasks_in_flight: number;
  uptime: string;
  last_task: string;
}

export const WORKER_PODS: WorkerPod[] = [
  { id: "server_worker-0", host: "k8s-node-01", status: "running", tasks_in_flight: 3, uptime: "17д", last_task: "ipmi.probe · 5s ago" },
  { id: "server_worker-1", host: "k8s-node-01", status: "running", tasks_in_flight: 1, uptime: "17д", last_task: "ssh.exec · 12s ago" },
  { id: "server_worker-2", host: "k8s-node-02", status: "idle", tasks_in_flight: 0, uptime: "17д", last_task: "—" },
  { id: "server_worker-3", host: "k8s-node-02", status: "running", tasks_in_flight: 2, uptime: "17д", last_task: "credential.rotate · 1m ago" },
];

export const CRON_JOBS = [
  { name: "audit.sweep", schedule: "0 3 * * *", last: "04.06 03:00", status: "ok" },
  { name: "rotation.master", schedule: "0 2 * * 0", last: "05.06 02:00", status: "ok" },
  { name: "backup.pg", schedule: "0 1 * * *", last: "10.06 01:00", status: "ok" },
  { name: "drill.restore", schedule: "0 0 1 * *", last: "01.06 00:00", status: "pass" },
  { name: "outbox.drain", schedule: "*/1 * * * *", last: "10.06 11:59", status: "ok" },
];

export const DLQ_POLICIES = [
  { name: "default", max_retries: 5, backoff: "exp(1,2,4,8,16)m", target: "dlq.default" },
  { name: "credential.rotate", max_retries: 8, backoff: "exp(1,2,4,8,16,32,64,128)s", target: "dlq.rotate" },
  { name: "ipmi.exec", max_retries: 3, backoff: "linear 30s", target: "dlq.ipmi" },
];

export interface ServerGroup {
  id: string;
  name: string;
  dept: string;
  size: number;
  note: string;
}

export const SERVER_GROUPS: ServerGroup[] = [
  { id: "core-pg", name: "core-pg", dept: "core", size: 6, note: "PostgreSQL kernel cluster" },
  { id: "dtkk-prod", name: "dtkk-prod", dept: "dtkk", size: 14, note: "ДТКК prod servers" },
  { id: "infra-bmc-rack-A", name: "infra-bmc-rack-A", dept: "infra", size: 18, note: "BMC-managed rack A" },
  { id: "infra-bmc-rack-B", name: "infra-bmc-rack-B", dept: "infra", size: 18, note: "BMC-managed rack B" },
  { id: "ops-monitoring", name: "ops-monitoring", dept: "ops", size: 4, note: "Prometheus + Grafana" },
];

export interface ServiceRoleDef {
  id: string;
  name: string;
  level: "admin" | "operator" | "reader" | "rotator";
  description: string;
  assigned: number;
}

export const SERVER_ROLES: ServiceRoleDef[] = [
  { id: "srv-admin", name: "server.admin", level: "admin", description: "CRUD по серверам, ролям, группам", assigned: 3 },
  { id: "srv-operator", name: "server.operator", level: "operator", description: "перезагрузка, IPMI-команды", assigned: 7 },
  { id: "srv-reader", name: "server.reader", level: "reader", description: "просмотр инвентаря", assigned: 12 },
  { id: "srv-rotator", name: "server.rotator", level: "rotator", description: "запуск rotate per-server", assigned: 4 },
];

export const SECRET_ROLES: ServiceRoleDef[] = [
  { id: "sec-admin", name: "secret.admin", level: "admin", description: "CRUD credentials + ACL", assigned: 2 },
  { id: "sec-rotator", name: "secret.rotator", level: "rotator", description: "запуск ротаций", assigned: 5 },
  { id: "sec-reader", name: "secret.reader", level: "reader", description: "list (без reveal)", assigned: 9 },
  { id: "sec-reveal", name: "secret.reveal", level: "operator", description: "reveal plaintext (audited)", assigned: 3 },
];

export const LOGING_ROLES: ServiceRoleDef[] = [
  { id: "log-admin", name: "loging.admin", level: "admin", description: "правила, retention, partitioning", assigned: 1 },
  { id: "log-reader", name: "loging.reader", level: "reader", description: "просмотр аудит-событий", assigned: 6 },
];

export const WORKER_ROLES: ServiceRoleDef[] = [
  { id: "wrk-admin", name: "worker.admin", level: "admin", description: "управление DLQ, retry-политиками, cron", assigned: 2 },
  { id: "wrk-operator", name: "worker.operator", level: "operator", description: "manual run task, drain", assigned: 4 },
  { id: "wrk-reader", name: "worker.reader", level: "reader", description: "task list / status", assigned: 8 },
];

export const PLATFORM_ROLES = [
  { id: "account_admin", description: "Глобальный аккаунт-админ — видит всё, тригерит ротации, бэкапы, миграции.", assigned: 1, members: ["bob"] },
  { id: "dep_admin", description: "Админ одного депа — управляет своими user/server/secret в scope.", assigned: 4, members: ["alice", "—"] },
  { id: "logging_admin", description: "Полный доступ к аудиту, правилам и retention.", assigned: 1, members: ["carol"] },
  { id: "logging_reader", description: "Read-only доступ к аудит-событиям.", assigned: 1, members: ["dave"] },
  { id: "logging_reader_dep", description: "Read-only аудит своего отдела (dept-scoped). Без правил и retention.", assigned: 0, members: ["—"] },
];
