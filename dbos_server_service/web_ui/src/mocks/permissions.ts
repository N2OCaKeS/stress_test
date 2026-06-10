/* Permission graph mock — groups, role definitions, assignments, direct grants, bots. */

export type Service = "auth" | "server" | "secret" | "logging" | "worker";

export interface MockGroup {
  id: string;
  name: string;
  description: string;
  owner_dept: string | null; // null = cross-dept
  cross_dept: boolean;
}

export interface RoleDef {
  id: string;
  name: string;
  service: Service | "platform";
  scope: "platform" | "dept" | "resource";
  description: string;
  permissions: string[]; // permission strings, e.g. "read:server:*"
}

export interface RoleAssignment {
  role_id: string;
  scope_kind: "platform" | "dept" | "resource";
  scope_ref: string | null; // dept_id, resource_id or null for platform
  granted_by: string;
  granted_at: string;
}

export interface DirectGrant {
  id: string;
  subject_kind: "user" | "bot" | "group";
  subject_id: string;
  permission: string;
  resource_kind: "server" | "secret" | "bot" | "audit";
  resource_id: string;
  granted_by: string;
  granted_at: string;
}

export interface MockBot {
  id: string;
  name: string;
  owner_dept: string;
  token_status: "active" | "rotated" | "revoked";
  last_used: string;
  created_at: string;
  created_by: string;
  initial_spec: string[]; // permissions at issuance — for drift detection
}

export const GROUPS: MockGroup[] = [
  {
    id: "g-core-ops",
    name: "core-ops",
    description: "Ops-команда Ядро DBOS — управление серверами и секретами депа",
    owner_dept: "core",
    cross_dept: false,
  },
  {
    id: "g-core-reviewers",
    name: "core-reviewers",
    description: "Ревьюеры конфигов в Ядро DBOS — read-only ко всему депу",
    owner_dept: "core",
    cross_dept: false,
  },
  {
    id: "g-dtkk-qa",
    name: "dtkk-qa",
    description: "QA-инженеры ДТКК — доступ к тестовым стендам",
    owner_dept: "dtkk",
    cross_dept: false,
  },
  {
    id: "g-dtkk-leads",
    name: "dtkk-leads",
    description: "Тимлиды ДТКК — управление пользователями депа",
    owner_dept: "dtkk",
    cross_dept: false,
  },
  {
    id: "g-infra-sre",
    name: "infra-sre",
    description: "SRE Инфры — full control над серверами депа Инфра",
    owner_dept: "infra",
    cross_dept: false,
  },
  {
    id: "g-cross-audit-readers",
    name: "cross-audit-readers",
    description: "Кросс-отдельная группа: чтение аудит-логов всей платформы",
    owner_dept: null,
    cross_dept: true,
  },
  {
    id: "g-cross-secret-readers",
    name: "cross-secret-readers",
    description: "Кросс-отдельные читатели секретов (например, compliance-офицеры)",
    owner_dept: null,
    cross_dept: true,
  },
  {
    id: "g-cross-incident-response",
    name: "incident-response",
    description: "Сводная on-call группа — повышенные права во время инцидента",
    owner_dept: null,
    cross_dept: true,
  },
];

export const ROLES: RoleDef[] = [
  // Платформенные роли
  {
    id: "r-account-admin",
    name: "account_admin",
    service: "platform",
    scope: "platform",
    description: "Полный контроль над всеми отделами и аккаунтами",
    permissions: [
      "write:user:*",
      "write:dept:*",
      "write:bot:*",
      "read:audit:*",
      "write:audit:rules",
      "read:server:*",
      "read:secret:*",
    ],
  },
  {
    id: "r-dep-admin",
    name: "dep_admin",
    service: "platform",
    scope: "dept",
    description: "Полное управление пользователями, ботами и ресурсами своего депа",
    permissions: [
      "write:user:dept",
      "write:bot:dept",
      "read:server:dept",
      "write:server:dept",
      "read:secret:dept",
      "write:secret:dept",
    ],
  },
  {
    id: "r-loging-admin",
    name: "loging_admin",
    service: "logging",
    scope: "platform",
    description: "Управление аудитом — правила, retention, чтение всех событий",
    permissions: ["read:audit:*", "write:audit:rules", "write:audit:retention"],
  },
  {
    id: "r-loging-reader",
    name: "loging_reader",
    service: "logging",
    scope: "platform",
    description: "Read-only доступ к аудит-каналу",
    permissions: ["read:audit:*"],
  },
  // Сервис-роли
  {
    id: "r-svc-server-operator",
    name: "server-operator",
    service: "server",
    scope: "dept",
    description: "Перезагрузка, диагностика и BMC-операции в рамках депа",
    permissions: [
      "read:server:dept",
      "exec:server:reboot",
      "exec:server:bmc",
    ],
  },
  {
    id: "r-svc-server-viewer",
    name: "server-viewer",
    service: "server",
    scope: "dept",
    description: "Read-only по серверам депа",
    permissions: ["read:server:dept"],
  },
  {
    id: "r-svc-secret-rotator",
    name: "secret-rotator",
    service: "secret",
    scope: "resource",
    description: "Ротация конкретного секрета без права чтения",
    permissions: ["rotate:secret:resource"],
  },
  {
    id: "r-svc-worker-operator",
    name: "worker-operator",
    service: "worker",
    scope: "dept",
    description: "Управление воркерами и DLQ депа",
    permissions: [
      "read:worker:dept",
      "exec:worker:requeue",
      "write:worker:config",
    ],
  },
];

export interface UserAssignments {
  groups: string[];
  roles: RoleAssignment[];
}

// user_id → { groups, roles }
export const USER_ASSIGNMENTS: Record<string, UserAssignments> = {
  "u-bob": {
    groups: [],
    roles: [
      {
        role_id: "r-account-admin",
        scope_kind: "platform",
        scope_ref: null,
        granted_by: "system",
        granted_at: "2025-09-01T10:00:00Z",
      },
    ],
  },
  "u-carol": {
    groups: ["g-cross-audit-readers"],
    roles: [
      {
        role_id: "r-loging-admin",
        scope_kind: "platform",
        scope_ref: null,
        granted_by: "u-bob",
        granted_at: "2025-11-04T09:12:00Z",
      },
    ],
  },
  "u-dave": {
    groups: ["g-cross-audit-readers"],
    roles: [
      {
        role_id: "r-loging-reader",
        scope_kind: "platform",
        scope_ref: null,
        granted_by: "u-bob",
        granted_at: "2025-11-04T09:12:00Z",
      },
    ],
  },
  "u-alice": {
    groups: ["g-core-ops"],
    roles: [
      {
        role_id: "r-dep-admin",
        scope_kind: "dept",
        scope_ref: "core",
        granted_by: "u-bob",
        granted_at: "2025-12-01T11:00:00Z",
      },
    ],
  },
  "u-core-1": {
    groups: ["g-core-ops", "g-cross-secret-readers"],
    roles: [
      {
        role_id: "r-svc-server-operator",
        scope_kind: "dept",
        scope_ref: "core",
        granted_by: "u-alice",
        granted_at: "2026-01-15T09:00:00Z",
      },
    ],
  },
  "u-core-2": {
    groups: ["g-core-reviewers"],
    roles: [],
  },
  "u-core-3": {
    groups: ["g-core-ops", "g-cross-incident-response"],
    roles: [
      {
        role_id: "r-svc-worker-operator",
        scope_kind: "dept",
        scope_ref: "core",
        granted_by: "u-alice",
        granted_at: "2026-02-10T14:00:00Z",
      },
    ],
  },
  "u-igor": {
    groups: ["g-dtkk-qa", "g-cross-secret-readers"],
    roles: [
      {
        role_id: "r-svc-secret-rotator",
        scope_kind: "dept",
        scope_ref: "dtkk",
        granted_by: "u-bob",
        granted_at: "2026-03-22T16:30:00Z",
      },
    ],
  },
  "u-dtkk-1": {
    groups: ["g-dtkk-leads"],
    roles: [
      {
        role_id: "r-dep-admin",
        scope_kind: "dept",
        scope_ref: "dtkk",
        granted_by: "u-bob",
        granted_at: "2025-12-15T10:00:00Z",
      },
    ],
  },
  "u-dtkk-2": {
    groups: ["g-dtkk-qa"],
    roles: [],
  },
  "u-pavel": {
    groups: ["g-infra-sre", "g-cross-incident-response"],
    roles: [
      {
        role_id: "r-svc-server-operator",
        scope_kind: "dept",
        scope_ref: "infra",
        granted_by: "u-bob",
        granted_at: "2026-02-01T09:00:00Z",
      },
      {
        role_id: "r-svc-secret-rotator",
        scope_kind: "dept",
        scope_ref: "infra",
        granted_by: "u-bob",
        granted_at: "2026-02-01T09:05:00Z",
      },
    ],
  },
  "u-infra-1": {
    groups: ["g-infra-sre"],
    roles: [
      {
        role_id: "r-dep-admin",
        scope_kind: "dept",
        scope_ref: "infra",
        granted_by: "u-bob",
        granted_at: "2025-12-20T11:00:00Z",
      },
    ],
  },
};

// group_id → roles[]
export const GROUP_ASSIGNMENTS: Record<string, RoleAssignment[]> = {
  "g-core-ops": [
    {
      role_id: "r-svc-server-operator",
      scope_kind: "dept",
      scope_ref: "core",
      granted_by: "u-alice",
      granted_at: "2025-12-05T10:00:00Z",
    },
    {
      role_id: "r-svc-worker-operator",
      scope_kind: "dept",
      scope_ref: "core",
      granted_by: "u-alice",
      granted_at: "2025-12-05T10:00:00Z",
    },
  ],
  "g-core-reviewers": [
    {
      role_id: "r-svc-server-viewer",
      scope_kind: "dept",
      scope_ref: "core",
      granted_by: "u-alice",
      granted_at: "2026-01-10T09:00:00Z",
    },
  ],
  "g-dtkk-qa": [
    {
      role_id: "r-svc-server-viewer",
      scope_kind: "dept",
      scope_ref: "dtkk",
      granted_by: "u-dtkk-1",
      granted_at: "2026-01-12T10:00:00Z",
    },
  ],
  "g-dtkk-leads": [
    {
      role_id: "r-svc-worker-operator",
      scope_kind: "dept",
      scope_ref: "dtkk",
      granted_by: "u-bob",
      granted_at: "2025-12-15T10:00:00Z",
    },
  ],
  "g-infra-sre": [
    {
      role_id: "r-svc-server-operator",
      scope_kind: "dept",
      scope_ref: "infra",
      granted_by: "u-infra-1",
      granted_at: "2026-01-05T09:00:00Z",
    },
    {
      role_id: "r-svc-worker-operator",
      scope_kind: "dept",
      scope_ref: "infra",
      granted_by: "u-infra-1",
      granted_at: "2026-01-05T09:00:00Z",
    },
  ],
  "g-cross-audit-readers": [
    {
      role_id: "r-loging-reader",
      scope_kind: "platform",
      scope_ref: null,
      granted_by: "u-bob",
      granted_at: "2025-10-01T10:00:00Z",
    },
  ],
  "g-cross-secret-readers": [
    {
      role_id: "r-svc-secret-rotator",
      scope_kind: "resource",
      scope_ref: "secret:db-master",
      granted_by: "u-bob",
      granted_at: "2026-02-20T10:00:00Z",
    },
  ],
  "g-cross-incident-response": [
    {
      role_id: "r-svc-server-operator",
      scope_kind: "platform",
      scope_ref: null,
      granted_by: "u-bob",
      granted_at: "2026-03-01T10:00:00Z",
    },
  ],
};

// group_id → user_ids
export const GROUP_MEMBERS: Record<string, string[]> = {
  "g-core-ops": ["u-alice", "u-core-1", "u-core-3"],
  "g-core-reviewers": ["u-core-2"],
  "g-dtkk-qa": ["u-igor", "u-dtkk-2"],
  "g-dtkk-leads": ["u-dtkk-1"],
  "g-infra-sre": ["u-pavel", "u-infra-1"],
  "g-cross-audit-readers": ["u-carol", "u-dave"],
  "g-cross-secret-readers": ["u-core-1", "u-igor"],
  "g-cross-incident-response": ["u-core-3", "u-pavel"],
};

export const DIRECT_GRANTS: DirectGrant[] = [
  {
    id: "grant-1",
    subject_kind: "user",
    subject_id: "u-core-1",
    permission: "read:server:web-01-prod",
    resource_kind: "server",
    resource_id: "web-01-prod",
    granted_by: "u-alice",
    granted_at: "2026-04-12T10:00:00Z",
  },
  {
    id: "grant-2",
    subject_kind: "user",
    subject_id: "u-igor",
    permission: "rotate:secret:db-master",
    resource_kind: "secret",
    resource_id: "db-master",
    granted_by: "u-bob",
    granted_at: "2026-05-01T14:20:00Z",
  },
  {
    id: "grant-3",
    subject_kind: "bot",
    subject_id: "b-ansible-core",
    permission: "exec:server:reboot",
    resource_kind: "server",
    resource_id: "core-*",
    granted_by: "u-alice",
    granted_at: "2026-01-10T08:00:00Z",
  },
  {
    id: "grant-4",
    subject_kind: "bot",
    subject_id: "b-prometheus",
    permission: "read:server:*",
    resource_kind: "server",
    resource_id: "*",
    granted_by: "u-bob",
    granted_at: "2025-11-15T11:00:00Z",
  },
  {
    id: "grant-5",
    subject_kind: "group",
    subject_id: "g-infra-sre",
    permission: "read:server:web-*",
    resource_kind: "server",
    resource_id: "web-*",
    granted_by: "u-bob",
    granted_at: "2026-03-18T12:00:00Z",
  },
  {
    id: "grant-6",
    subject_kind: "group",
    subject_id: "g-core-ops",
    permission: "read:secret:db-master",
    resource_kind: "secret",
    resource_id: "db-master",
    granted_by: "u-alice",
    granted_at: "2026-04-02T09:00:00Z",
  },
];

export const BOTS: MockBot[] = [
  {
    id: "b-ansible-core",
    name: "bot-ansible-core",
    owner_dept: "core",
    token_status: "active",
    last_used: "2026-06-10T08:00:00Z",
    created_at: "2026-01-10T08:00:00Z",
    created_by: "u-alice",
    initial_spec: ["exec:server:reboot", "read:server:dept"],
  },
  {
    id: "b-ci-core",
    name: "bot-ci-core",
    owner_dept: "core",
    token_status: "active",
    last_used: "2026-06-10T09:00:00Z",
    created_at: "2026-01-12T09:00:00Z",
    created_by: "u-alice",
    initial_spec: ["read:server:dept"],
  },
  {
    id: "b-jira-dtkk",
    name: "bot-jira-dtkk",
    owner_dept: "dtkk",
    token_status: "active",
    last_used: "2026-06-10T09:10:00Z",
    created_at: "2026-02-01T10:00:00Z",
    created_by: "u-dtkk-1",
    initial_spec: ["read:audit:*"],
  },
  {
    id: "b-build-dtkk",
    name: "bot-build-dtkk",
    owner_dept: "dtkk",
    token_status: "rotated",
    last_used: "2026-06-10T08:50:00Z",
    created_at: "2026-02-15T11:00:00Z",
    created_by: "u-dtkk-1",
    initial_spec: ["read:server:dept", "read:secret:dept"],
  },
  {
    id: "b-prometheus",
    name: "bot-prometheus",
    owner_dept: "infra",
    token_status: "active",
    last_used: "2026-06-10T09:11:00Z",
    created_at: "2025-11-15T11:00:00Z",
    created_by: "u-infra-1",
    initial_spec: ["read:server:dept"],
  },
  {
    id: "b-backup",
    name: "bot-backup",
    owner_dept: "infra",
    token_status: "revoked",
    last_used: "2026-05-30T01:00:00Z",
    created_at: "2025-12-01T08:00:00Z",
    created_by: "u-infra-1",
    initial_spec: ["read:server:dept", "read:secret:dept"],
  },
];

// bot_id → roles & groups
export const BOT_ASSIGNMENTS: Record<string, { groups: string[]; roles: RoleAssignment[] }> = {
  "b-ansible-core": {
    groups: [],
    roles: [
      {
        role_id: "r-svc-server-operator",
        scope_kind: "dept",
        scope_ref: "core",
        granted_by: "u-alice",
        granted_at: "2026-01-10T08:00:00Z",
      },
    ],
  },
  "b-ci-core": {
    groups: [],
    roles: [
      {
        role_id: "r-svc-server-viewer",
        scope_kind: "dept",
        scope_ref: "core",
        granted_by: "u-alice",
        granted_at: "2026-01-12T09:00:00Z",
      },
    ],
  },
  "b-jira-dtkk": {
    groups: [],
    roles: [
      {
        role_id: "r-loging-reader",
        scope_kind: "platform",
        scope_ref: null,
        granted_by: "u-bob",
        granted_at: "2026-02-01T10:00:00Z",
      },
    ],
  },
  "b-build-dtkk": {
    groups: [],
    roles: [
      {
        role_id: "r-svc-server-viewer",
        scope_kind: "dept",
        scope_ref: "dtkk",
        granted_by: "u-dtkk-1",
        granted_at: "2026-02-15T11:00:00Z",
      },
    ],
  },
  "b-prometheus": {
    groups: [],
    roles: [
      {
        role_id: "r-svc-server-viewer",
        scope_kind: "platform",
        scope_ref: null,
        granted_by: "u-bob",
        granted_at: "2025-11-15T11:00:00Z",
      },
    ],
  },
  "b-backup": {
    groups: [],
    roles: [
      {
        role_id: "r-svc-server-viewer",
        scope_kind: "dept",
        scope_ref: "infra",
        granted_by: "u-infra-1",
        granted_at: "2025-12-01T08:00:00Z",
      },
    ],
  },
};

// User-extension: groups list overlay onto MockUser (auth.ts).
// Maps user_id → groups[] (subset of USER_ASSIGNMENTS).
export function userGroups(userId: string): string[] {
  return USER_ASSIGNMENTS[userId]?.groups ?? [];
}

// MFA status (mock)
export const USER_MFA: Record<string, boolean> = {
  "u-bob": true,
  "u-carol": true,
  "u-alice": true,
  "u-pavel": true,
  "u-igor": false,
  "u-dave": false,
};

// Audit-streaks per user (mock)
export interface AuditEvent {
  id: string;
  ts: string;
  action: string;
  resource: string;
  outcome: "success" | "denied" | "error";
}

export const USER_AUDIT: Record<string, AuditEvent[]> = {
  "u-alice": [
    { id: "a1", ts: "2026-06-10T07:50:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "a2", ts: "2026-06-10T07:52:00Z", action: "user.create", resource: "u-core-7", outcome: "success" },
    { id: "a3", ts: "2026-06-10T07:55:00Z", action: "server.reboot", resource: "core-db-02", outcome: "success" },
    { id: "a4", ts: "2026-06-10T08:01:00Z", action: "secret.read", resource: "core-app-token", outcome: "success" },
    { id: "a5", ts: "2026-06-10T08:10:00Z", action: "group.add_member", resource: "g-core-ops/u-core-3", outcome: "success" },
    { id: "a6", ts: "2026-06-10T08:30:00Z", action: "audit.read", resource: "filter:dept=core", outcome: "denied" },
    { id: "a7", ts: "2026-06-09T17:00:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "a8", ts: "2026-06-09T17:30:00Z", action: "server.bmc.power_cycle", resource: "core-web-01", outcome: "success" },
    { id: "a9", ts: "2026-06-09T18:00:00Z", action: "secret.rotate", resource: "core-postgres", outcome: "success" },
    { id: "a10", ts: "2026-06-09T18:30:00Z", action: "logout", resource: "/ui", outcome: "success" },
  ],
  "u-bob": [
    { id: "b1", ts: "2026-06-10T08:12:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "b2", ts: "2026-06-10T08:15:00Z", action: "user.create", resource: "u-infra-5", outcome: "success" },
    { id: "b3", ts: "2026-06-10T08:20:00Z", action: "dept.delete", resource: "ops-legacy", outcome: "denied" },
    { id: "b4", ts: "2026-06-10T08:25:00Z", action: "cluster.rotation.trigger", resource: "platform-master-key", outcome: "success" },
    { id: "b5", ts: "2026-06-10T08:40:00Z", action: "tls.renew", resource: "emm.devos.astralinux.ru", outcome: "success" },
    { id: "b6", ts: "2026-06-09T21:05:00Z", action: "user.create", resource: "u-dtkk-12", outcome: "success" },
    { id: "b7", ts: "2026-06-09T20:00:00Z", action: "role.grant", resource: "r-dep-admin → u-infra-1", outcome: "success" },
    { id: "b8", ts: "2026-06-09T15:00:00Z", action: "dept.create", resource: "ops", outcome: "success" },
    { id: "b9", ts: "2026-06-08T11:00:00Z", action: "bot.revoke", resource: "b-backup", outcome: "success" },
    { id: "b10", ts: "2026-06-08T09:30:00Z", action: "audit.export", resource: "csv:weekly-2026w23", outcome: "success" },
    { id: "b11", ts: "2026-06-08T08:00:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "b12", ts: "2026-06-07T16:42:00Z", action: "tls.renew", resource: "internal-ca", outcome: "success" },
  ],
  "u-carol": [
    { id: "c1", ts: "2026-06-10T09:30:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "c2", ts: "2026-06-10T09:33:00Z", action: "audit.search", resource: "filter:severity=ERROR", outcome: "success" },
    { id: "c3", ts: "2026-06-10T09:40:00Z", action: "audit.rule.create", resource: "rule-failed-login-5", outcome: "success" },
    { id: "c4", ts: "2026-06-10T09:50:00Z", action: "audit.retention.update", resource: "core: 90d", outcome: "success" },
    { id: "c5", ts: "2026-06-09T14:00:00Z", action: "audit.export", resource: "csv:2026-06-01..2026-06-08", outcome: "success" },
  ],
  "u-dave": [
    { id: "d1", ts: "2026-06-10T07:30:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "d2", ts: "2026-06-10T07:33:00Z", action: "audit.query", resource: "filter:service=secret&since=24h", outcome: "success" },
    { id: "d3", ts: "2026-06-10T07:45:00Z", action: "audit.view.event_detail", resource: "ev-0089", outcome: "success" },
    { id: "d4", ts: "2026-06-10T07:55:00Z", action: "audit.query", resource: "filter:actor=u-igor&action=secret.*", outcome: "success" },
    { id: "d5", ts: "2026-06-10T08:10:00Z", action: "audit.export", resource: "csv:2026-06-09..2026-06-10&dept=dtkk", outcome: "success" },
    { id: "d6", ts: "2026-06-09T17:42:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "d7", ts: "2026-06-09T18:00:00Z", action: "audit.query", resource: "filter:severity=CRITICAL", outcome: "success" },
    { id: "d8", ts: "2026-06-09T18:30:00Z", action: "audit.view.event_detail", resource: "ev-0144", outcome: "success" },
    { id: "d9", ts: "2026-06-09T19:00:00Z", action: "audit.rule.update", resource: "rule-failed-login-5", outcome: "denied" },
    { id: "d10", ts: "2026-06-08T14:15:00Z", action: "audit.query", resource: "filter:result=denied&window=24h", outcome: "success" },
    { id: "d11", ts: "2026-06-08T14:40:00Z", action: "audit.export", resource: "jsonl:2026-06-07", outcome: "success" },
  ],
  "u-igor": [
    { id: "e1", ts: "2026-06-10T06:11:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "e2", ts: "2026-06-10T06:18:00Z", action: "secret.create", resource: "dtkk-stand-token-12", outcome: "success" },
    { id: "e3", ts: "2026-06-10T06:35:00Z", action: "secret.rotate", resource: "dtkk-ci-secret", outcome: "success" },
    { id: "e4", ts: "2026-06-10T07:02:00Z", action: "secret.policy.update", resource: "rotation:dtkk-ci-secret=30d", outcome: "success" },
    { id: "e5", ts: "2026-06-10T07:20:00Z", action: "secret.rotate", resource: "db-master", outcome: "success" },
    { id: "e6", ts: "2026-06-09T16:00:00Z", action: "secret.read", resource: "db-master", outcome: "denied" },
    { id: "e7", ts: "2026-06-09T16:10:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "e8", ts: "2026-06-09T16:30:00Z", action: "secret.create", resource: "dtkk-build-token-4", outcome: "success" },
    { id: "e9", ts: "2026-06-09T17:00:00Z", action: "secret.policy.update", resource: "min-length:32", outcome: "success" },
    { id: "e10", ts: "2026-06-08T10:00:00Z", action: "secret.rotate", resource: "dtkk-stand-token-7", outcome: "error" },
    { id: "e11", ts: "2026-06-08T10:05:00Z", action: "secret.rotate", resource: "dtkk-stand-token-7", outcome: "success" },
    { id: "e12", ts: "2026-06-08T11:30:00Z", action: "secret.policy.update", resource: "rotation:dtkk-stand-*=14d", outcome: "success" },
  ],
  "u-pavel": [
    { id: "f1", ts: "2026-06-10T08:01:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "f2", ts: "2026-06-10T08:05:00Z", action: "server.reboot", resource: "infra-mon-01", outcome: "success" },
    { id: "f3", ts: "2026-06-10T08:20:00Z", action: "secret.read", resource: "infra-grafana-key", outcome: "success" },
    { id: "f4", ts: "2026-06-10T08:35:00Z", action: "server.bmc.power_cycle", resource: "infra-edge-03", outcome: "success" },
    { id: "f5", ts: "2026-06-10T08:50:00Z", action: "secret.rotate", resource: "infra-grafana-key", outcome: "success" },
    { id: "f6", ts: "2026-06-09T22:15:00Z", action: "server.reboot", resource: "infra-edge-07", outcome: "error" },
    { id: "f7", ts: "2026-06-09T22:20:00Z", action: "server.reboot", resource: "infra-edge-07", outcome: "success" },
    { id: "f8", ts: "2026-06-09T15:00:00Z", action: "secret.create", resource: "infra-monitoring-token", outcome: "success" },
    { id: "f9", ts: "2026-06-09T15:10:00Z", action: "server.bmc.diag", resource: "infra-db-01", outcome: "success" },
    { id: "f10", ts: "2026-06-08T19:00:00Z", action: "audit.read", resource: "filter:dept=infra&window=24h", outcome: "denied" },
    { id: "f11", ts: "2026-06-08T11:00:00Z", action: "login", resource: "/ui", outcome: "success" },
    { id: "f12", ts: "2026-06-08T11:30:00Z", action: "server.power_off", resource: "infra-stage-04", outcome: "success" },
  ],
};

// Deterministic pseudo-events for ordinary dept users — no card listed
// above but UserDetail still wants to show a streak. Seeded by user_id so
// rendering is stable across reloads.
const ORDINARY_ACTIONS_BY_DEPT: Record<string, string[]> = {
  core: ["server.read", "worker.requeue", "audit.query", "secret.read"],
  dtkk: ["server.read", "secret.read", "audit.query", "worker.read"],
  infra: ["server.read", "server.bmc.diag", "secret.read", "audit.query"],
  ops: ["server.read", "worker.read", "audit.query"],
};

function seededInt(seed: string, salt: number): number {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
  }
  return Math.abs(h);
}

export function syntheticUserAudit(
  userId: string,
  deptId: string | null,
  count = 5,
): AuditEvent[] {
  const dept = deptId ?? "ops";
  const actions = ORDINARY_ACTIONS_BY_DEPT[dept] ?? ORDINARY_ACTIONS_BY_DEPT.ops;
  const out: AuditEvent[] = [];
  for (let i = 0; i < count; i++) {
    const a = actions[seededInt(userId, i) % actions.length];
    const hoursAgo = (seededInt(userId, i + 17) % 70) + 1;
    const ts = new Date(Date.UTC(2026, 5, 10, 9, 0) - hoursAgo * 3600_000)
      .toISOString()
      .replace(/\.\d{3}Z$/, "Z");
    const denied = seededInt(userId, i + 31) % 11 === 0;
    out.push({
      id: `syn-${userId}-${i + 1}`,
      ts,
      action: a,
      resource: `${dept}-target-${(seededInt(userId, i + 47) % 12) + 1}`,
      outcome: denied ? "denied" : "success",
    });
  }
  return out.sort((x, y) => (x.ts < y.ts ? 1 : -1));
}

// Resolve audit for any user — explicit entry if exists, otherwise
// generate a stable synthetic streak.
export function auditForUser(
  userId: string,
  deptId: string | null,
): AuditEvent[] {
  const explicit = USER_AUDIT[userId];
  if (explicit && explicit.length > 0) return explicit;
  return syntheticUserAudit(userId, deptId);
}

// Resources for matrix
export interface ResourceRef {
  id: string;
  kind: "server" | "secret" | "dept";
  name: string;
  dept?: string;
}

export const RESOURCES: ResourceRef[] = [
  { id: "core-web-01", kind: "server", name: "core-web-01", dept: "core" },
  { id: "core-db-02", kind: "server", name: "core-db-02", dept: "core" },
  { id: "dtkk-stand-1", kind: "server", name: "dtkk-stand-1", dept: "dtkk" },
  { id: "infra-mon-01", kind: "server", name: "infra-mon-01", dept: "infra" },
  { id: "db-master", kind: "secret", name: "db-master", dept: "core" },
  { id: "core-app-token", kind: "secret", name: "core-app-token", dept: "core" },
  { id: "dtkk-ci-secret", kind: "secret", name: "dtkk-ci-secret", dept: "dtkk" },
  { id: "infra-grafana-key", kind: "secret", name: "infra-grafana-key", dept: "infra" },
];
