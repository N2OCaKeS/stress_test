import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Search,
  Crown,
  Building2,
  UserCog,
  User,
  Bot,
  UserPlus,
  KeyRound,
  Pause,
  LogOut,
  Edit3,
  Trash2,
  Mail,
  Clock,
  ShieldCheck,
  Cog,
  Monitor,
  Chrome,
  UsersRound,
  FolderPlus,
  FileText,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { GROUPS, BOTS } from "@/mocks/permissions";
import { USERS } from "@/mocks/auth";
import {
  deleteUser,
  disableUser,
  listUsers,
  listUserSessions,
  resetUserPassword,
  revokeUserSessionById,
  revokeUserSessions,
} from "@/api/auth/users";
import { listBots } from "@/api/auth/bots";
import { listGroups } from "@/api/auth/groups";
import { listDepartments } from "@/api/auth/departments";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { useDeptLabel, useLabelsInvalidate } from "@/lib/labels";
import { formatMskDate, formatMskShort } from "@/lib/datetime";
import { apiErrMsg } from "@/api/client";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { userMutationCaps, groupMutationCaps, botMutationCaps, personaDeptId } from "@/lib/rbac";
import type {
  Bot as ApiBot,
  Department,
  Group as ApiGroup,
  PlatformRole,
  User as ApiUser,
} from "@/api/auth/types";
import {
  CreateBotForm,
  CreateGroupForm,
  CreateUserForm,
  EditRolesForm,
  Modal,
} from "./_userActions";

/**
 * Port of users-account_admin.html (bob).
 * Middle: users grouped by dept (платформенные + 3 депа + гости).
 * Workzone: profile of carol (selected) with identification, roles, sessions, PATs.
 */
type RowKind = "ok" | "warn" | "danger";

interface UserRow {
  name: string;
  role?: { label: string; kind?: "warn" | "accent" | "plain" };
  dept: string;
  lastSeen: string;
  status: { label: string; kind: RowKind };
  isBot?: boolean;
  isCog?: boolean;
  active?: boolean;
}

interface UserGroup {
  icon: "crown" | "building";
  label: string;
  rows: UserRow[];
}

const GROUPS_MOCK: UserGroup[] = [
  {
    icon: "crown",
    label: "Платформенные · 4",
    rows: [
      { name: "bob", role: { label: "account_admin", kind: "warn" }, dept: "—", lastSeen: "сейчас", status: { label: "active", kind: "ok" }, isCog: true },
      { name: "carol", role: { label: "loging_admin", kind: "accent" }, dept: "—", lastSeen: "14 ч", status: { label: "active", kind: "ok" }, isCog: true, active: true },
      { name: "liam", role: { label: "loging_reader", kind: "plain" }, dept: "—", lastSeen: "3 ч", status: { label: "active", kind: "ok" }, isCog: true },
      { name: "quinn", role: { label: "loging_reader", kind: "plain" }, dept: "—", lastSeen: "4 ч", status: { label: "active", kind: "ok" }, isCog: true },
    ],
  },
  {
    icon: "building",
    label: "Ядро DBOS · 8",
    rows: [
      { name: "alice", role: { label: "dep_admin", kind: "plain" }, dept: "Ядро DBOS", lastSeen: "5 мин", status: { label: "active", kind: "ok" }, isCog: true },
      { name: "dave", dept: "Ядро DBOS", lastSeen: "1 ч", status: { label: "active", kind: "ok" } },
      { name: "igor", dept: "Ядро DBOS", lastSeen: "20 мин", status: { label: "active", kind: "ok" } },
      { name: "pavel", dept: "Ядро DBOS", lastSeen: "3 дн", status: { label: "blocked", kind: "warn" } },
      { name: "grace", dept: "Ядро DBOS", lastSeen: "45 мин", status: { label: "active", kind: "ok" } },
      { name: "henry", dept: "Ядро DBOS", lastSeen: "2 ч", status: { label: "active", kind: "ok" } },
      { name: "bot-ansible-core", role: { label: "bot", kind: "plain" }, dept: "Ядро DBOS", lastSeen: "10 мин", status: { label: "active", kind: "ok" }, isBot: true },
      { name: "bot-ci-core", role: { label: "bot", kind: "plain" }, dept: "Ядро DBOS", lastSeen: "2 мин", status: { label: "active", kind: "ok" }, isBot: true },
    ],
  },
  {
    icon: "building",
    label: "ДТКК · 12",
    rows: [
      { name: "sam", role: { label: "dep_admin", kind: "plain" }, dept: "ДТКК", lastSeen: "30 мин", status: { label: "active", kind: "ok" }, isCog: true },
      { name: "tina", dept: "ДТКК", lastSeen: "5 мин", status: { label: "active", kind: "ok" } },
      { name: "ulrich", dept: "ДТКК", lastSeen: "40 мин", status: { label: "active", kind: "ok" } },
      { name: "vera", dept: "ДТКК", lastSeen: "1 ч", status: { label: "active", kind: "ok" } },
      { name: "walter", dept: "ДТКК", lastSeen: "15 мин", status: { label: "active", kind: "ok" } },
      { name: "xenia", dept: "ДТКК", lastSeen: "3 ч", status: { label: "active", kind: "ok" } },
      { name: "yara", dept: "ДТКК", lastSeen: "14 дн", status: { label: "banned", kind: "danger" } },
      { name: "zane", dept: "ДТКК", lastSeen: "2 ч", status: { label: "active", kind: "ok" } },
      { name: "artem", dept: "ДТКК", lastSeen: "4 ч", status: { label: "active", kind: "ok" } },
      { name: "boris", dept: "ДТКК", lastSeen: "5 дн", status: { label: "blocked", kind: "warn" } },
      { name: "bot-jira-dtkk", role: { label: "bot", kind: "plain" }, dept: "ДТКК", lastSeen: "1 мин", status: { label: "active", kind: "ok" }, isBot: true },
      { name: "bot-build-dtkk", role: { label: "bot", kind: "plain" }, dept: "ДТКК", lastSeen: "7 мин", status: { label: "active", kind: "ok" }, isBot: true },
    ],
  },
  {
    icon: "building",
    label: "Инфра · 15",
    rows: [
      { name: "kate", role: { label: "dep_admin", kind: "plain" }, dept: "Инфра", lastSeen: "10 мин", status: { label: "active", kind: "ok" }, isCog: true },
      { name: "leo", dept: "Инфра", lastSeen: "2 мин", status: { label: "active", kind: "ok" } },
      { name: "maya", dept: "Инфра", lastSeen: "25 мин", status: { label: "active", kind: "ok" } },
      { name: "nick", dept: "Инфра", lastSeen: "1 ч", status: { label: "active", kind: "ok" } },
      { name: "oksana", dept: "Инфра", lastSeen: "50 мин", status: { label: "active", kind: "ok" } },
      { name: "pavel", dept: "Инфра", lastSeen: "3 ч", status: { label: "active", kind: "ok" } },
      { name: "rita", dept: "Инфра", lastSeen: "1 ч", status: { label: "active", kind: "ok" } },
      { name: "stepan", dept: "Инфра", lastSeen: "20 мин", status: { label: "active", kind: "ok" } },
      { name: "tanya", dept: "Инфра", lastSeen: "5 ч", status: { label: "active", kind: "ok" } },
      { name: "vlad", dept: "Инфра", lastSeen: "1 дн", status: { label: "blocked", kind: "warn" } },
      { name: "wanda", dept: "Инфра", lastSeen: "2 ч", status: { label: "active", kind: "ok" } },
      { name: "xavier", dept: "Инфра", lastSeen: "4 ч", status: { label: "active", kind: "ok" } },
      { name: "bot-prometheus", role: { label: "bot", kind: "plain" }, dept: "Инфра", lastSeen: "30 сек", status: { label: "active", kind: "ok" }, isBot: true },
      { name: "bot-grafana-sync", role: { label: "bot", kind: "plain" }, dept: "Инфра", lastSeen: "3 мин", status: { label: "active", kind: "ok" }, isBot: true },
      { name: "bot-backup", role: { label: "bot", kind: "plain" }, dept: "Инфра", lastSeen: "1 ч", status: { label: "active", kind: "ok" }, isBot: true },
    ],
  },
  {
    icon: "building",
    label: "Гость · 3",
    rows: [
      { name: "guest_audit", dept: "Гость", lastSeen: "7 дн", status: { label: "active", kind: "ok" } },
      { name: "guest_demo", dept: "Гость", lastSeen: "2 дн", status: { label: "active", kind: "ok" } },
      { name: "guest_oldcontractor", dept: "Гость", lastSeen: "30 дн", status: { label: "banned", kind: "danger" } },
    ],
  },
];

function apiToRow(u: ApiUser): UserRow {
  const statusKind: RowKind =
    u.status === "ACTIVE" ? "ok" : u.status === "BLOCKED" ? "warn" : "danger";
  return {
    name: u.username,
    role: u.platform_role
      ? {
          label: u.platform_role,
          kind: u.platform_role === "account_admin" ? "warn" : "accent",
        }
      : undefined,
    dept: u.department_name ?? u.department_id ?? "—",
    lastSeen: formatMskDate(u.updated_at ?? u.created_at),
    status: { label: u.status.toLowerCase(), kind: statusKind },
    isCog: !!u.platform_role,
  };
}

function badgeClass(kind?: "warn" | "accent" | "plain"): string {
  if (kind === "warn") return "badge badge-warn";
  if (kind === "accent") return "badge badge-accent";
  return "badge";
}

type Tab = "users" | "groups" | "bots";
type WorkzoneTab = "profile" | "roles" | "sessions" | "pats" | "bots" | "audit";

const WORKZONE_TABS: { id: WorkzoneTab; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "roles", label: "Roles & Grants" },
  { id: "sessions", label: "Sessions" },
  { id: "pats", label: "PATs" },
  { id: "bots", label: "Bots created" },
  { id: "audit", label: "Audit" },
];

export function UsersAccountAdmin() {
  const [tab, setTab] = useState<Tab>("users");
  const [workzoneTab, setWorkzoneTab] = useState<WorkzoneTab>("profile");
  const mockMode = useMockMode();
  const toast = useToast();
  const { persona } = usePersona();
  const invalidateLabels = useLabelsInvalidate();

  // API-backed list (skipped in mock mode). When enabled, the left aside
  // renders flat groups by dept built from the API response.
  const apiUsersQ = useQuery(
    () => listUsers({ limit: 200, include_banned: true }),
    [],
    { enabled: !mockMode },
  );
  const apiDeptsQ = useQuery<Department[]>(
    () => listDepartments(),
    [],
    { enabled: !mockMode },
  );
  // Список групп нужен и для счётчика на вкладке Groups, и для рендера самой
  // вкладки в live-режиме. В mock-режиме не дёргаем.
  const apiGroupsQ = useQuery(
    () => listGroups({ limit: 200 }),
    [],
    { enabled: !mockMode },
  );
  // Аналогично для ботов.
  const apiBotsQ = useQuery(
    () => listBots({ limit: 200 }),
    [],
    { enabled: !mockMode },
  );

  // The workzone shows carol as a representative target. In live mode we look
  // her up in the API result; in mock mode we fall back to the static USERS
  // fixture. All header buttons act on this user.
  const targetUser = useMemo(() => {
    if (mockMode) {
      const m = USERS.find((u) => u.username === "carol");
      if (!m) return null;
      return {
        id: m.id,
        username: m.username,
        email: m.email ?? `${m.username}@dbos.local`,
        platform_role: (m.platform_role ?? null) as PlatformRole,
        dept_id: m.dept_id,
        dept_name: null as string | null,
        status: "ACTIVE",
        created_at: null as string | null,
        updated_at: null as string | null,
        created_by: null as string | null,
        must_change_password: false,
      };
    }
    const items = apiUsersQ.data?.items ?? [];
    const u = items[0];
    if (!u) return null;
    return {
      id: u.id,
      username: u.username,
      email: u.email ?? "",
      platform_role: u.platform_role,
      dept_id: u.department_id,
      dept_name: u.department_name ?? null,
      status: u.status,
      created_at: u.created_at ?? null,
      updated_at: u.updated_at ?? null,
      created_by: null,
      must_change_password: u.must_change_password ?? false,
    };
  }, [mockMode, apiUsersQ.data]);

  const caps = useMemo(
    () => userMutationCaps(persona, targetUser?.dept_id ?? null),
    [persona, targetUser?.dept_id],
  );

  // Sessions for the workzone Sessions tab. Mock mode keeps the static table.
  const sessionsQ = useQuery(
    () => listUserSessions(targetUser!.id),
    [targetUser?.id, workzoneTab],
    { enabled: !mockMode && !!targetUser && workzoneTab === "sessions" },
  );

  // Боты, созданные target-пользователем — фильтруем общий список ботов
  // (apiBotsQ) по created_by. Отдельного запроса больше не делаем.
  const botsCreatedByTarget = useMemo(() => {
    if (!targetUser) return [];
    return (apiBotsQ.data ?? []).filter((b) => b.created_by === targetUser.id);
  }, [apiBotsQ.data, targetUser]);

  // For Groups/Bots the "Создать" button is gated on the persona's own dept
  // scope (account_admin == any dept; dep_admin == own dept only).
  const myDept = useMemo(() => personaDeptId(persona), [persona]);
  const groupCaps = useMemo(
    () => groupMutationCaps(persona, myDept),
    [persona, myDept],
  );
  const botCaps = useMemo(
    () => botMutationCaps(persona, myDept),
    [persona, myDept],
  );

  const [busy, setBusy] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editRolesOpen, setEditRolesOpen] = useState(false);

  async function runAction(label: string, fn: () => Promise<unknown>) {
    if (mockMode) {
      toast.info(`mock: ${label}`);
      return;
    }
    setBusy(label);
    try {
      await fn();
      toast.success(`${label}: OK`);
      apiUsersQ.refetch();
    } catch (e) {
      const msg = apiErrMsg(e);
      toast.error(`${label}: ${msg}`);
    } finally {
      setBusy(null);
    }
  }

  const deptsLite = useMemo(() => {
    if (mockMode) return [];
    return (apiDeptsQ.data ?? []).map((d) => ({
      id: d.id,
      name: d.name,
    }));
  }, [mockMode, apiDeptsQ.data]);

  const tgtLabel = targetUser ? targetUser.username : "пользователь";

  function handleResetPassword() {
    if (!targetUser) return;
    const pwd = window.prompt(`Новый пароль для ${tgtLabel} (min 12, буквы + цифры):`);
    if (!pwd) return;
    runAction("reset-password", () =>
      resetUserPassword(targetUser.id, { new_password: pwd }),
    );
  }

  function handleBlock() {
    if (!targetUser) return;
    if (!window.confirm(`Заблокировать ${tgtLabel}?`)) return;
    runAction("disable", () => disableUser(targetUser.id));
  }

  function handleRevokeSessions() {
    if (!targetUser) return;
    if (!window.confirm(`Revoke all sessions для ${tgtLabel}?`)) return;
    runAction("revoke-sessions", () => revokeUserSessions(targetUser.id));
  }

  function handleDelete() {
    if (!targetUser) return;
    const reason = window.prompt(
      `Hard-delete ${tgtLabel}: укажи причину (Q3 reorg / left / ...):`,
    );
    if (!reason) return;
    if (!window.confirm(`Снести ${tgtLabel} целиком?`)) return;
    runAction("delete", () => deleteUser(targetUser.id, { reason }));
  }

  function handleSessionRevoke(sessionId: string) {
    if (!targetUser) return;
    runAction(`revoke-session ${sessionId.slice(0, 8)}`, () =>
      revokeUserSessionById(targetUser.id, sessionId),
    );
  }

  function handlePatRevoke(tokenId: string) {
    // No admin endpoint for revoking another user's PAT — see obsidian/TODO.md.
    toast.warn(
      `revoke PAT ${tokenId}: admin endpoint отсутствует (есть только /me/tokens)`,
    );
  }

  const apiGroups = useMemo<UserGroup[]>(() => {
    if (mockMode) return [];
    const users = apiUsersQ.data?.items ?? [];
    const depts = apiDeptsQ.data ?? [];
    const out: UserGroup[] = [];
    const platform = users.filter((u) => !u.department_id);
    if (platform.length) {
      out.push({
        icon: "crown",
        label: `Платформенные · ${platform.length}`,
        rows: platform.map(apiToRow),
      });
    }
    for (const d of depts) {
      const dusers = users.filter((u) => u.department_id === d.id);
      if (dusers.length === 0) continue;
      out.push({
        icon: "building",
        label: `${d.name} · ${dusers.length}`,
        rows: dusers.map(apiToRow),
      });
    }
    return out;
  }, [mockMode, apiUsersQ.data, apiDeptsQ.data]);

  const renderGroups = mockMode ? GROUPS_MOCK : apiGroups;
  const usersCount = mockMode
    ? USERS.length
    : apiUsersQ.data?.total ?? apiUsersQ.data?.items.length ?? 0;
  const groupsCount = mockMode ? GROUPS.length : (apiGroupsQ.data ?? []).length;
  const botsCount = mockMode ? BOTS.length : (apiBotsQ.data ?? []).length;

  return (
    <Shell breadcrumb="auth_service / users">
      <aside className="w-[360px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2 flex flex-col gap-2">
          <div className="flex gap-1">
            <TabBtn icon={<User className="w-3 h-3" />} active={tab === "users"} onClick={() => setTab("users")}>Users · {usersCount}</TabBtn>
            <TabBtn icon={<UsersRound className="w-3 h-3" />} active={tab === "groups"} onClick={() => setTab("groups")}>Groups · {groupsCount}</TabBtn>
            <TabBtn icon={<Bot className="w-3 h-3" />} active={tab === "bots"} onClick={() => setTab("bots")}>Bots · {botsCount}</TabBtn>
          </div>
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder={`Поиск ${tab === "users" ? "пользователей" : tab === "groups" ? "групп" : "ботов"}...`}
            />
          </div>
          <div className="text-[10px] text-dim text-center pt-1">
            Матрицы доступа теперь живут внутри карточки пользователя — вкладка «Матрица доступа».
          </div>
          {!mockMode && apiUsersQ.loading && (
            <div className="spinner" aria-label="Loading" />
          )}
          {!mockMode && apiUsersQ.error && (
            <div className="alert-danger text-[11px]">
              {apiUsersQ.error.message}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {tab === "users" && renderGroups.length === 0 && !apiUsersQ.loading && (
            <div className="empty-card text-xs mx-3">Нет данных</div>
          )}
          {tab === "users" && renderGroups.map((g, gi) => (
            <div key={g.label}>
              <div className={`group-header flex items-center gap-2 ${gi > 0 ? "mt-3" : ""}`}>
                {g.icon === "crown" ? (
                  <Crown className="w-3 h-3 text-warn" />
                ) : (
                  <Building2 className="w-3 h-3" />
                )}
                {g.label}
              </div>
              <div className="px-2 flex flex-col gap-0.5">
                {g.rows.map((row) => {
                  // В live-режиме apiUsersQ уже знает реальные id; в mock-моде
                  // фолбэк на статический USERS для перехода в карточку.
                  const apiUser = !mockMode
                    ? (apiUsersQ.data?.items ?? []).find(
                        (u) => u.username === row.name,
                      )
                    : null;
                  const mockUser = mockMode
                    ? USERS.find((u) => u.username === row.name)
                    : null;
                  const real = apiUser ?? mockUser;
                  const linkTo = real ? `/users/${real.id}` : `/users/${row.name}`;
                  return (
                    <Link
                      to={linkTo}
                      key={`${g.label}-${row.name}`}
                      className={`cred-row ${row.active ? "active" : ""}`}
                    >
                      <div className="flex items-center gap-2">
                        {row.isBot ? (
                          <Bot className="w-4 h-4 text-dim" />
                        ) : row.isCog ? (
                          <UserCog
                            className={`w-4 h-4 ${
                              row.role?.kind === "warn" ? "text-warn" : row.active ? "text-accent" : "text-dim"
                            }`}
                          />
                        ) : (
                          <User className="w-4 h-4 text-dim" />
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="text-sm truncate flex items-center gap-2">
                            <span className={row.isBot ? "mono" : ""}>{row.name}</span>
                            {row.role && (
                              <span className={badgeClass(row.role.kind)}>
                                {row.role.label}
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-dim flex items-center gap-2">
                            <span>{row.dept}</span>
                            <span>·</span>
                            <span>{row.lastSeen}</span>
                          </div>
                        </div>
                        <span className={`badge badge-${row.status.kind}`}>
                          {row.status.label}
                        </span>
                      </div>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}

          {tab === "groups" && (
            <div className="px-2 flex flex-col gap-0.5">
              <div className="group-header flex items-center gap-2">
                <UsersRound className="w-3 h-3" /> Все группы · {groupsCount}
              </div>
              {mockMode
                ? GROUPS.map((g) => (
                    <Link key={g.id} to={`/users/group/${g.id}`} className="cred-row">
                      <div className="flex items-center gap-2">
                        <UsersRound className="w-4 h-4 text-accent" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm truncate flex items-center gap-2">
                            <span>{g.name}</span>
                            {g.cross_dept ? (
                              <span className="badge badge-warn">cross</span>
                            ) : (
                              <span className="badge">{g.owner_dept}</span>
                            )}
                          </div>
                          <div className="text-[11px] text-dim truncate">{g.description}</div>
                        </div>
                      </div>
                    </Link>
                  ))
                : apiGroupsQ.loading
                  ? <div className="spinner mx-3" aria-label="Loading" />
                  : apiGroupsQ.error
                    ? <div className="alert-danger text-[11px] mx-3">{apiGroupsQ.error.message}</div>
                    : (apiGroupsQ.data ?? []).length === 0
                      ? <div className="empty-card text-xs mx-3">Групп нет</div>
                      : (apiGroupsQ.data ?? []).map((g) => (
                          <Link key={g.id} to={`/users/group/${g.id}`} className="cred-row">
                            <GroupAsideRow group={g} />
                          </Link>
                        ))}
            </div>
          )}

          {tab === "bots" && (
            <div className="px-2 flex flex-col gap-0.5">
              <div className="group-header flex items-center gap-2">
                <Bot className="w-3 h-3" /> Все боты · {botsCount}
              </div>
              {mockMode
                ? BOTS.map((b) => (
                    <Link key={b.id} to={`/users/bot/${b.id}`} className="cred-row">
                      <div className="flex items-center gap-2">
                        <Bot className="w-4 h-4 text-dim" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm truncate mono">{b.name}</div>
                          <div className="text-[11px] text-dim">{b.owner_dept} · {formatMskDate(b.last_used)}</div>
                        </div>
                        <span className={`badge badge-${b.token_status === "active" ? "ok" : b.token_status === "rotated" ? "warn" : "danger"}`}>
                          {b.token_status}
                        </span>
                      </div>
                    </Link>
                  ))
                : apiBotsQ.loading
                  ? <div className="spinner mx-3" aria-label="Loading" />
                  : apiBotsQ.error
                    ? <div className="alert-danger text-[11px] mx-3">{apiBotsQ.error.message}</div>
                    : (apiBotsQ.data ?? []).length === 0
                      ? <div className="empty-card text-xs mx-3">Ботов нет</div>
                      : (apiBotsQ.data ?? []).map((b) => (
                          <Link key={b.id} to={`/users/bot/${b.id}`} className="cred-row">
                            <BotAsideRow bot={b} />
                          </Link>
                        ))}
            </div>
          )}
        </div>

        <div className="border-t border-token p-3">
          {(() => {
            const cfg =
              tab === "groups"
                ? {
                    icon: <FolderPlus className="w-4 h-4" />,
                    label: "Создать группу",
                    allowed: groupCaps.edit,
                    reason: groupCaps.reason,
                    hint: "группа всегда привязана к dept-у владельцу",
                  }
                : tab === "bots"
                  ? {
                      icon: <Bot className="w-4 h-4" />,
                      label: "Создать бота",
                      allowed: botCaps.rotateToken,
                      reason: botCaps.reason,
                      hint: "бот принадлежит dept-у, первый токен показывается один раз",
                    }
                  : {
                      icon: <UserPlus className="w-4 h-4" />,
                      label: "Создать пользователя",
                      allowed: caps.edit,
                      reason: caps.reason,
                      hint: "account_admin может создавать платформенных и дептовых пользователей",
                    };
            return (
              <>
                <button
                  className="btn btn-primary w-full flex items-center justify-center gap-2"
                  disabled={!cfg.allowed}
                  title={cfg.allowed ? undefined : cfg.reason}
                  onClick={() => setCreateOpen(true)}
                >
                  {cfg.icon}
                  {cfg.label}
                </button>
                <div className="text-[10px] text-dim mt-1 text-center">
                  {cfg.hint}
                </div>
              </>
            );
          })()}
        </div>
      </aside>

      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="border-b border-token p-5 flex items-start gap-4">
          <div className="w-12 h-12 rounded-full bg-accent flex items-center justify-center text-base font-semibold">
            {(targetUser?.username ?? "??").slice(0, 2).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate">
                {targetUser?.username ?? "—"}
              </h1>
              {targetUser && (
                <span
                  className={`badge badge-${targetUser.status === "ACTIVE" || targetUser.status === "active" ? "ok" : targetUser.status === "BLOCKED" || targetUser.status === "blocked" ? "warn" : "danger"}`}
                >
                  {String(targetUser.status).toLowerCase()}
                </span>
              )}
              {targetUser?.platform_role && (
                <span className="badge badge-accent">{targetUser.platform_role}</span>
              )}
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="flex items-center gap-1">
                <Mail className="w-3 h-3" />{" "}
                {targetUser?.email || "—"}
              </span>
              <span>·</span>
              <span className="mono">{targetUser?.id ?? "—"}</span>
              <span>·</span>
              <span>
                dept:{" "}
                <b>{targetUser?.dept_name ?? (targetUser?.dept_id ? targetUser.dept_id : "—")}</b>
                {!targetUser?.dept_id && " (платформенный)"}
              </span>
              {targetUser?.updated_at && (
                <>
                  <span>·</span>
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3" /> {formatMskShort(targetUser.updated_at)}
                  </span>
                </>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.edit || busy !== null || !targetUser}
              title={caps.edit ? undefined : caps.reason}
              onClick={handleResetPassword}
            >
              <KeyRound className="w-4 h-4" /> Reset password
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={!caps.disable || busy !== null || !targetUser}
              title={caps.disable ? undefined : caps.reason}
              onClick={handleBlock}
            >
              <Pause className="w-4 h-4" /> Block
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={!caps.disable || busy !== null || !targetUser}
              title={caps.disable ? undefined : caps.reason}
              onClick={handleRevokeSessions}
            >
              <LogOut className="w-4 h-4" /> Revoke sessions
            </button>
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.manageRoles || busy !== null || !targetUser}
              title={caps.manageRoles ? undefined : caps.reason}
              onClick={() => setEditRolesOpen(true)}
            >
              <Edit3 className="w-4 h-4" /> Edit roles
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={!caps.delete || busy !== null || !targetUser}
              title={caps.delete ? undefined : caps.reason}
              onClick={handleDelete}
            >
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        </div>

        <div className="border-b border-token px-5 flex gap-1 flex-wrap">
          {WORKZONE_TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setWorkzoneTab(t.id)}
              className={`px-3 py-2 text-sm border-b-2 -mb-px ${
                workzoneTab === t.id
                  ? "border-accent text-accent"
                  : "border-transparent text-dim hover:text-soft"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="scroll-block p-5 grid grid-cols-2 gap-5">
          {workzoneTab === "profile" && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <User className="w-4 h-4" /> Идентификация
              </div>
              {targetUser ? (
                <div className="grid grid-cols-2 gap-x-6 text-sm">
                  <div>
                    <StatRow k="username" v={<span className="mono">{targetUser.username}</span>} />
                    <StatRow k="email" v={<span className="mono">{targetUser.email || "—"}</span>} />
                    <StatRow k="ID" v={<span className="mono">{targetUser.id}</span>} />
                    <StatRow
                      k="dept_id"
                      v={
                        <span className="mono text-dim">
                          {targetUser.dept_id ?? "— (платформенный)"}
                        </span>
                      }
                    />
                  </div>
                  <div>
                    <StatRow k="created_at" v={targetUser.created_at ?? "—"} />
                    <StatRow k="created_by" v={<span className="mono">{targetUser.created_by ?? "—"}</span>} />
                    <StatRow
                      k="status"
                      v={
                        <span
                          className={`badge badge-${String(targetUser.status).toLowerCase() === "active" ? "ok" : String(targetUser.status).toLowerCase() === "blocked" ? "warn" : "danger"}`}
                        >
                          {String(targetUser.status).toLowerCase()}
                        </span>
                      }
                    />
                    <StatRow
                      k="must_change_password"
                      v={
                        <span className="text-dim">
                          {targetUser.must_change_password ? "да" : "нет"}
                        </span>
                      }
                    />
                  </div>
                </div>
              ) : (
                <div className="empty-card text-xs">Пользователь не выбран.</div>
              )}
            </div>
          )}

          {workzoneTab === "roles" && (
            <>
              <div className="surface border border-token rounded-lg p-4">
                <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4" /> Платформенные роли
                </div>
                <div className="text-sm">
                  {targetUser?.platform_role ? (
                    <div className="flex items-center gap-2 mb-2">
                      <span className="badge badge-accent">{targetUser.platform_role}</span>
                    </div>
                  ) : (
                    <div className="text-xs text-dim italic">
                      Платформенные роли не назначены.
                    </div>
                  )}
                </div>
              </div>

              <div className="surface border border-token rounded-lg p-4">
                <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                  <Cog className="w-4 h-4" /> Service-роли
                </div>
                <div className="text-sm text-dim italic">
                  Сервис-роли подтянутся из API (endpoint ещё не подключён к этому экрану).
                </div>
                <button
                  className="btn mt-3 w-full flex items-center justify-center gap-2"
                  disabled={!caps.manageRoles || !targetUser}
                  title={caps.manageRoles ? undefined : caps.reason}
                  onClick={() => setEditRolesOpen(true)}
                >
                  <UserPlus className="w-4 h-4" /> Добавить роль
                </button>
              </div>
            </>
          )}

          {workzoneTab === "sessions" && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <Monitor className="w-4 h-4" /> Последние сессии
              </div>
              {mockMode ? (
                <table className="w-full text-sm">
                  <thead className="text-left text-dim text-xs uppercase">
                    <tr>
                      <th className="pb-2 pr-3">IP</th>
                      <th className="pb-2 pr-3">UA</th>
                      <th className="pb-2 pr-3">Started</th>
                      <th className="pb-2 pr-3">Last seen</th>
                      <th className="pb-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-t border-token">
                      <td className="py-2 mono">10.177.103.42</td>
                      <td className="flex items-center gap-1 py-2">
                        <Chrome className="w-3 h-3" /> Mac · Chrome 132
                      </td>
                      <td className="text-dim text-xs">14 ч назад</td>
                      <td>
                        <span className="text-ok">активна</span>
                      </td>
                      <td>
                        <button
                          className="btn btn-danger text-xs"
                          disabled={!caps.disable || !targetUser}
                          title={caps.disable ? undefined : caps.reason}
                          onClick={() => handleSessionRevoke("ses_demo_active")}
                        >
                          revoke
                        </button>
                      </td>
                    </tr>
                    <tr className="border-t border-token">
                      <td className="py-2 mono">10.177.103.42</td>
                      <td className="flex items-center gap-1 py-2">
                        <Chrome className="w-3 h-3" /> Mac · Chrome 132
                      </td>
                      <td className="text-dim text-xs">3 дн назад</td>
                      <td className="text-dim text-xs">2 дн назад</td>
                      <td>
                        <button className="btn text-xs" disabled>
                          закрыта
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              ) : sessionsQ.loading ? (
                <div className="spinner" aria-label="Loading" />
              ) : sessionsQ.error ? (
                <div className="alert-danger text-[11px]">{sessionsQ.error.message}</div>
              ) : (sessionsQ.data?.items ?? []).length === 0 ? (
                <div className="empty-card text-xs">Нет активных сессий</div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-dim text-xs uppercase">
                    <tr>
                      <th className="pb-2 pr-3">IP</th>
                      <th className="pb-2 pr-3">UA</th>
                      <th className="pb-2 pr-3">Started</th>
                      <th className="pb-2 pr-3">Last seen</th>
                      <th className="pb-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {(sessionsQ.data?.items ?? []).map((s) => (
                      <tr key={s.session_id} className="border-t border-token">
                        <td className="py-2 mono">{s.ip_address ?? "—"}</td>
                        <td className="py-2 text-xs text-dim truncate max-w-[280px]">
                          {s.user_agent ?? "—"}
                        </td>
                        <td className="text-dim text-xs">{formatMskShort(s.created_at)}</td>
                        <td className="text-dim text-xs">
                          {formatMskShort(s.last_used_at)}
                        </td>
                        <td>
                          <button
                            className="btn btn-danger text-xs"
                            disabled={!caps.disable || !targetUser}
                            title={caps.disable ? undefined : caps.reason}
                            onClick={() => handleSessionRevoke(s.session_id)}
                          >
                            revoke
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {workzoneTab === "pats" && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <KeyRound className="w-4 h-4" /> Активные PATs
              </div>
              {mockMode ? (
                <table className="w-full text-sm">
                  <thead className="text-left text-dim text-xs uppercase">
                    <tr>
                      <th className="pb-2 pr-3">Name</th>
                      <th className="pb-2 pr-3">Created</th>
                      <th className="pb-2 pr-3">Last used</th>
                      <th className="pb-2 pr-3">Scopes</th>
                      <th className="pb-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-t border-token">
                      <td className="py-2 mono">grafana-loki-dashboard</td>
                      <td className="text-dim text-xs">2026-04-12</td>
                      <td className="text-dim text-xs">5 мин</td>
                      <td>
                        <span className="badge badge-accent">logs:read</span>{" "}
                        <span className="badge">logs:export</span>
                      </td>
                      <td>
                        <button
                          className="btn btn-danger text-xs"
                          disabled={!caps.delete || !targetUser}
                          title={caps.delete ? undefined : caps.reason}
                          onClick={() => handlePatRevoke("pat_grafana_loki")}
                        >
                          revoke
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              ) : (
                <div className="empty-card text-xs">
                  Admin-просмотр чужих PAT недоступен — есть только `/me/tokens`.
                  Попроси пользователя проверить токены в своём профиле.
                </div>
              )}
            </div>
          )}

          {workzoneTab === "bots" && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <Bot className="w-4 h-4" /> Боты, созданные {tgtLabel}
              </div>
              {mockMode ? (
                <div className="text-sm text-dim italic">
                  mock: список ботов будет подтянут из API в live-mode.
                </div>
              ) : apiBotsQ.loading ? (
                <div className="spinner" aria-label="Loading" />
              ) : apiBotsQ.error ? (
                <div className="alert-danger text-[11px]">{apiBotsQ.error.message}</div>
              ) : botsCreatedByTarget.length === 0 ? (
                <div className="empty-card text-xs">
                  Этот пользователь не создавал ботов.
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-dim text-xs uppercase">
                    <tr>
                      <th className="pb-2 pr-3">Name</th>
                      <th className="pb-2 pr-3">Dept</th>
                      <th className="pb-2 pr-3">Status</th>
                      <th className="pb-2 pr-3">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {botsCreatedByTarget.map((b) => (
                      <tr key={b.id} className="border-t border-token">
                        <td className="py-2">
                          <Link to={`/users/bot/${b.id}`} className="mono text-accent">
                            {b.name}
                          </Link>
                        </td>
                        <td className="text-dim text-xs">
                          <BotDeptCell deptId={b.department_id} />
                        </td>
                        <td>
                          <span
                            className={`badge badge-${b.status === "active" ? "ok" : "warn"}`}
                          >
                            {b.status}
                          </span>
                        </td>
                        <td className="text-dim text-xs">{formatMskDate(b.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {workzoneTab === "audit" && targetUser && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <Clock className="w-4 h-4" /> Audit
              </div>
              <div className="text-sm text-dim">
                Лента audit-событий по этому пользователю живёт в loging_service.
              </div>
              <Link
                to={`/log?actor_id=${encodeURIComponent(targetUser.id)}`}
                className="btn mt-3 inline-flex items-center gap-2"
              >
                <FileText className="w-4 h-4" /> Открыть /log?actor_id={targetUser.id}
              </Link>
            </div>
          )}
        </div>
      </section>

      {createOpen && tab === "users" && (
        <Modal title="Новый пользователь" onClose={() => setCreateOpen(false)}>
          <CreateUserForm
            depts={deptsLite}
            mockMode={mockMode}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              if (mockMode) {
                toast.info("mock: create user");
              } else {
                toast.success("Пользователь создан");
                apiUsersQ.refetch();
              }
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "groups" && (
        <Modal title="Новая группа" onClose={() => setCreateOpen(false)}>
          <CreateGroupForm
            depts={deptsLite}
            mockMode={mockMode}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              if (mockMode) {
                toast.info("mock: create group");
              } else {
                toast.success("Группа создана");
                apiGroupsQ.refetch();
                void invalidateLabels("groups");
              }
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "bots" && (
        <Modal title="Новый бот" onClose={() => setCreateOpen(false)}>
          <CreateBotForm
            depts={deptsLite}
            mockMode={mockMode}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              if (mockMode) {
                toast.info("mock: create bot");
              } else {
                toast.success("Бот создан");
                apiBotsQ.refetch();
              }
            }}
          />
        </Modal>
      )}

      {editRolesOpen && targetUser && (
        <Modal
          title={`Edit roles · ${targetUser.username}`}
          onClose={() => setEditRolesOpen(false)}
        >
          <EditRolesForm
            user={{
              id: targetUser.id,
              username: targetUser.username,
              platform_role: targetUser.platform_role,
              dept_id: targetUser.dept_id,
            }}
            depts={deptsLite}
            mockMode={mockMode}
            onCancel={() => setEditRolesOpen(false)}
            onSuccess={() => {
              setEditRolesOpen(false);
              toast.success("Роли обновлены");
              apiUsersQ.refetch();
            }}
          />
        </Modal>
      )}
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

function TabBtn({
  icon,
  active,
  onClick,
  children,
}: {
  icon: React.ReactNode;
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 px-2 py-1 text-xs flex items-center justify-center gap-1 rounded border ${
        active ? "border-accent text-accent surface-2" : "border-token text-dim"
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

function BotDeptCell({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span>{label}</span>;
}

function GroupAsideRow({ group }: { group: ApiGroup }) {
  const dept = useDeptLabel(group.department_id);
  return (
    <div className="flex items-center gap-2">
      <UsersRound className="w-4 h-4 text-accent" />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate flex items-center gap-2">
          <span>{group.name}</span>
          <span className="badge">{dept}</span>
        </div>
        <div className="text-[11px] text-dim truncate">
          {group.description ?? group.name}
        </div>
      </div>
    </div>
  );
}

function BotAsideRow({ bot }: { bot: ApiBot }) {
  const dept = useDeptLabel(bot.department_id);
  return (
    <div className="flex items-center gap-2">
      <Bot className="w-4 h-4 text-dim" />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate mono">{bot.name}</div>
        <div className="text-[11px] text-dim">
          {dept} · {formatMskDate(bot.created_at)}
        </div>
      </div>
      <span
        className={`badge badge-${bot.status === "active" ? "ok" : "warn"}`}
      >
        {bot.status}
      </span>
    </div>
  );
}
