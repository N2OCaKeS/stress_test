/* Cluster-level mocks for the admin section: pod health plus the
   service/platform role catalogues and server groups used by the
   role-card and platform-roles pages. */

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

export interface ServerGroup {
  id: string;
  name: string;
  dept: string;
  size: number;
  note: string;
}

export const SERVER_GROUPS: ServerGroup[] = [
  { id: "core-pg", name: "core-pg", dept: "core", size: 6, note: "PostgreSQL kernel cluster" },
  { id: "dev-prod", name: "dev-prod", dept: "dev", size: 14, note: "Разработка prod servers" },
  { id: "infra-bmc-rack-A", name: "infra-bmc-rack-A", dept: "infra", size: 18, note: "BMC-managed rack A" },
  { id: "infra-bmc-rack-B", name: "infra-bmc-rack-B", dept: "infra", size: 18, note: "BMC-managed rack B" },
  { id: "ops-monitoring", name: "ops-monitoring", dept: "ops", size: 4, note: "Prometheus + Grafana" },
];

export interface ServiceRoleDef {
  id: string;
  name: string;
  level: "admin" | "rotator";
  description: string;
  assigned: number;
}

export const SERVER_ROLES: ServiceRoleDef[] = [
  { id: "srv-admin", name: "server.admin", level: "admin", description: "CRUD по серверам, ролям, группам", assigned: 3 },
  { id: "srv-rotator", name: "server.rotator", level: "rotator", description: "запуск rotate per-server", assigned: 4 },
];

export const SECRET_ROLES: ServiceRoleDef[] = [
  { id: "sec-admin", name: "secret.admin", level: "admin", description: "CRUD credentials + ACL", assigned: 2 },
  { id: "sec-rotator", name: "secret.rotator", level: "rotator", description: "запуск ротаций", assigned: 5 },
];

export const WORKER_ROLES: ServiceRoleDef[] = [
  { id: "wrk-admin", name: "worker.admin", level: "admin", description: "управление DLQ, retry-политиками, cron", assigned: 2 },
];

export const PLATFORM_ROLES = [
  { id: "account_admin", description: "Глобальный аккаунт-админ — видит всё, тригерит ротации, бэкапы, миграции.", assigned: 1, members: ["bob"] },
  { id: "dep_admin", description: "Админ одного депа — управляет своими user/server/secret в scope.", assigned: 4, members: ["alice", "—"] },
  { id: "logging_admin", description: "Полный доступ к аудиту, правилам и retention.", assigned: 1, members: ["carol"] },
  { id: "logging_reader", description: "Read-only доступ к аудит-событиям.", assigned: 1, members: ["dave"] },
  { id: "logging_reader_dep", description: "Read-only аудит своего отдела (dept-scoped). Без правил и retention.", assigned: 0, members: ["—"] },
];
