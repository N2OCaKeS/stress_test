/* auth_service mock: 47 users across 4 depts. */

export interface MockDept {
  id: string;
  name: string;
  description: string;
  user_count: number;
}

export interface MockUser {
  id: string;
  username: string;
  email: string;
  dept_id: string | null;
  platform_role: string | null;
  status: "active" | "blocked" | "banned" | "pending";
  last_login: string;
  is_bot: boolean;
  created_by: string | null;
  /** Groups (auth_service) the user belongs to. Optional — defaults to []. */
  groups?: string[];
  mfa_enabled?: boolean;
}

export function userById(id: string): MockUser | undefined {
  return USERS.find((u) => u.id === id);
}
export function userByUsername(name: string): MockUser | undefined {
  return USERS.find((u) => u.username === name);
}

export const DEPTS: MockDept[] = [
  { id: "core", name: "Ядро DBOS", description: "Платформенная команда", user_count: 8 },
  { id: "dtkk", name: "ДТКК", description: "Дирекция тестирования и контроля качества", user_count: 14 },
  { id: "infra", name: "Инфра", description: "Инфраструктура и сети", user_count: 11 },
  { id: "ops", name: "Operations", description: "Эксплуатация", user_count: 14 },
];

const PLATFORM_USERS: MockUser[] = [
  { id: "u-bob", username: "bob", email: "bob@dbos.local", dept_id: null, platform_role: "account_admin", status: "active", last_login: "2026-06-10T08:12:00Z", is_bot: false, created_by: null, groups: [], mfa_enabled: true },
  { id: "u-carol", username: "carol", email: "carol@dbos.local", dept_id: null, platform_role: "logging_admin", status: "active", last_login: "2026-06-10T09:30:00Z", is_bot: false, created_by: "u-bob", groups: ["g-cross-audit-readers"], mfa_enabled: true },
  { id: "u-dave", username: "dave", email: "dave@dbos.local", dept_id: null, platform_role: "logging_reader", status: "active", last_login: "2026-06-09T17:42:00Z", is_bot: false, created_by: "u-bob", groups: ["g-cross-audit-readers"], mfa_enabled: false },
];

function fillDept(deptId: string, count: number, startIdx: number): MockUser[] {
  const out: MockUser[] = [];
  for (let i = 0; i < count; i++) {
    const idx = startIdx + i;
    const username = `${deptId}_user${i + 1}`;
    out.push({
      id: `u-${deptId}-${i + 1}`,
      username,
      email: `${username}@dbos.local`,
      dept_id: deptId,
      platform_role: i === 0 ? "dep_admin" : null,
      status: idx % 11 === 0 ? "blocked" : idx % 13 === 0 ? "pending" : "active",
      last_login: `2026-06-${String(8 + (idx % 3)).padStart(2, "0")}T${String(8 + (idx % 12)).padStart(2, "0")}:00:00Z`,
      is_bot: idx % 7 === 0,
      created_by: i === 0 ? "u-bob" : `u-${deptId}-1`,
    });
  }
  return out;
}

export const USERS: MockUser[] = [
  ...PLATFORM_USERS,
  { id: "u-alice", username: "alice", email: "alice@dbos.local", dept_id: "core", platform_role: "dep_admin", status: "active", last_login: "2026-06-10T07:50:00Z", is_bot: false, created_by: "u-bob" },
  ...fillDept("core", 7, 1),
  { id: "u-igor", username: "igor", email: "igor@dbos.local", dept_id: "dtkk", platform_role: null, status: "active", last_login: "2026-06-10T06:11:00Z", is_bot: false, created_by: "u-bob" },
  ...fillDept("dtkk", 13, 1),
  { id: "u-pavel", username: "pavel", email: "pavel@dbos.local", dept_id: "infra", platform_role: null, status: "active", last_login: "2026-06-10T08:01:00Z", is_bot: false, created_by: "u-bob" },
  ...fillDept("infra", 10, 1),
  ...fillDept("ops", 14, 1),
];
