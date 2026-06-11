import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Search,
  User,
  UsersRound,
  Building2,
  UserCog,
  Bot,
  UserPlus,
  KeyRound,
  Pause,
  LogOut,
  Mail,
  Clock,
  Shield,
  Cog,
  FileText,
  Monitor,
  Chrome,
  FolderPlus,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { GROUPS, BOTS } from "@/mocks/permissions";
import { USERS } from "@/mocks/auth";
import {
  disableUser,
  listUsersByDepartment,
  listUserSessions,
  normalizeUserStatus,
  resetUserPassword,
  revokeUserSessionById,
  revokeUserSessions,
  userStatusBadgeKind,
} from "@/api/auth/users";
import { listBotsWithTotal } from "@/api/auth/bots";
import { listGroupsWithTotal } from "@/api/auth/groups";
import { listDepartments } from "@/api/auth/departments";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { useDeptLabel, useLabelsInvalidate } from "@/lib/labels";
import { formatMskDate, formatMskShort } from "@/lib/datetime";
import { userMutationCaps, groupMutationCaps, botMutationCaps, personaDeptId } from "@/lib/rbac";
import type {
  Bot as ApiBot,
  Department,
  Group as ApiGroup,
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
 * Dept-scoped Users screen for department_admin (и любой не-account_admin).
 *
 * Mock-режим оставляет статичный мокап (alice / Ядро DBOS) для демо-персон.
 * Live-режим ходит в `GET /users/department/{persona.dept_id}` — глобальный
 * `/users` для dep_admin отдаёт 403 ROLE_REQUIRED. Юзеры группируются на
 * `my` (сам) и `my_dep` (остальные своего отдела); cross_dep всегда пуст —
 * dep_admin не видит чужие отделы.
 */
interface Row {
  name: string;
  role?: { label: string };
  dept: string;
  lastSeen: string;
  status: { label: string; kind: "ok" | "warn" | "danger" };
  isBot?: boolean;
  isCog?: boolean;
  cogColor?: "warn" | "accent" | "dim";
  active?: boolean;
}

const MY: Row[] = [
  {
    name: "alice",
    role: { label: "dep_admin" },
    dept: "Ядро DBOS",
    lastSeen: "сейчас",
    status: { label: "active", kind: "ok" },
    isCog: true,
    cogColor: "warn",
  },
];

const MY_DEP: Row[] = [
  { name: "dave", dept: "Ядро DBOS", lastSeen: "1 ч", status: { label: "active", kind: "ok" }, active: true },
  { name: "igor", dept: "Ядро DBOS", lastSeen: "20 мин", status: { label: "active", kind: "ok" } },
  { name: "pavel", dept: "Ядро DBOS", lastSeen: "3 дн", status: { label: "blocked", kind: "warn" } },
  { name: "grace", dept: "Ядро DBOS", lastSeen: "45 мин", status: { label: "active", kind: "ok" } },
  { name: "henry", dept: "Ядро DBOS", lastSeen: "2 ч", status: { label: "active", kind: "ok" } },
  { name: "bot-ansible-core", role: { label: "bot" }, dept: "Ядро DBOS", lastSeen: "10 мин", status: { label: "active", kind: "ok" }, isBot: true },
  { name: "bot-ci-core", role: { label: "bot" }, dept: "Ядро DBOS", lastSeen: "2 мин", status: { label: "active", kind: "ok" }, isBot: true },
];

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


export function UsersDepAdmin() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (mockMode) return <UsersDepAdminMock />;
  return <UsersDepAdminLive persona={persona} />;
}

// ---------------------------------------------------------------------------
// Live: dept-scoped, real APIs
// ---------------------------------------------------------------------------

function UsersDepAdminLive({ persona }: { persona: ReturnType<typeof usePersona>["persona"] }) {
  const toast = useToast();
  const invalidateLabels = useLabelsInvalidate();
  const [tab, setTab] = useState<Tab>("users");
  const [workzoneTab, setWorkzoneTab] = useState<WorkzoneTab>("profile");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editRolesOpen, setEditRolesOpen] = useState(false);

  const myDept = useMemo(() => personaDeptId(persona), [persona]);

  // Главный драйвер экрана — юзеры своего отдела. dep_admin не имеет доступа
  // к глобальному /users (403), поэтому ходим строго в свой отдел.
  const usersQ = useQuery(
    () => listUsersByDepartment(myDept!, { limit: 200, include_banned: true }),
    [myDept],
    { enabled: !!myDept },
  );

  const deptsQ = useQuery<Department[]>(() => listDepartments(), []);
  const groupsQ = useQuery(
    () => listGroupsWithTotal({ limit: 200 }),
    [],
    { enabled: tab === "groups" },
  );
  const botsQ = useQuery(() => listBotsWithTotal({ limit: 200 }), []);

  const users = usersQ.data?.items ?? [];

  // Группы депа — фильтруем общий список по своему отделу.
  const deptGroups = useMemo(
    () => (groupsQ.data?.items ?? []).filter((g) => g.department_id === myDept),
    [groupsQ.data, myDept],
  );
  // Боты депа — фильтруем по department_id.
  const deptBots = useMemo(
    () => (botsQ.data?.items ?? []).filter((b) => b.department_id === myDept),
    [botsQ.data, myDept],
  );

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return users;
    return users.filter((u) => u.username.toLowerCase().includes(q));
  }, [users, search]);

  // self vs остальные. dep_admin не видит чужие отделы — cross_dep всегда пуст.
  const myRow = useMemo(
    () => filteredUsers.find((u) => u.id === persona.id),
    [filteredUsers, persona.id],
  );
  const otherRows = useMemo(
    () => filteredUsers.filter((u) => u.id !== persona.id),
    [filteredUsers, persona.id],
  );

  // Выбранный в workzone юзер: явно кликнутый, иначе первый из отдела.
  const targetUser = useMemo(() => {
    if (users.length === 0) return null;
    const picked = selectedId
      ? users.find((u) => u.id === selectedId)
      : undefined;
    const u = picked ?? users[0];
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
      must_change_password: u.must_change_password ?? false,
    };
  }, [users, selectedId]);

  const caps = useMemo(
    () => userMutationCaps(persona, targetUser?.dept_id ?? null),
    [persona, targetUser?.dept_id],
  );
  // Self-ban guard: dep_admin не банит / не сбрасывает сам себя.
  const isSelfTarget = !!targetUser && targetUser.id === persona.id;

  const groupCaps = useMemo(() => groupMutationCaps(persona, myDept), [persona, myDept]);
  const botCaps = useMemo(() => botMutationCaps(persona, myDept), [persona, myDept]);

  const sessionsQ = useQuery(
    () => listUserSessions(targetUser!.id),
    [targetUser?.id, workzoneTab],
    { enabled: !!targetUser && workzoneTab === "sessions" },
  );

  const botsCreatedByTarget = useMemo(() => {
    if (!targetUser) return [];
    return (botsQ.data?.items ?? []).filter(
      (b) => b.created_by === targetUser.id,
    );
  }, [botsQ.data, targetUser]);

  // Create-user форма должна работать только в рамках своего отдела — передаём
  // в depts ровно один (свой) отдел, так dep_admin не выберет чужой.
  const deptsLite = useMemo(() => {
    const all = deptsQ.data ?? [];
    return all
      .filter((d) => d.id === myDept)
      .map((d) => ({ id: d.id, name: d.name }));
  }, [deptsQ.data, myDept]);

  async function runAction(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    try {
      await fn();
      toast.success(`${label}: OK`);
      usersQ.refetch();
    } catch (e) {
      const msg = apiErrMsg(e);
      toast.error(`${label}: ${msg}`);
    } finally {
      setBusy(null);
    }
  }

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
    if (!targetUser || isSelfTarget) return;
    if (!window.confirm(`Заблокировать ${tgtLabel}?`)) return;
    runAction("disable", () => disableUser(targetUser.id));
  }

  function handleRevokeSessions() {
    if (!targetUser) return;
    if (!window.confirm(`Revoke all sessions для ${tgtLabel}?`)) return;
    runAction("revoke-sessions", () => revokeUserSessions(targetUser.id));
  }

  function handleSessionRevoke(sessionId: string) {
    if (!targetUser) return;
    runAction(`revoke-session ${sessionId.slice(0, 8)}`, () =>
      revokeUserSessionById(targetUser.id, sessionId),
    );
  }

  const deptName = useDeptLabel(myDept);
  const usersCount = usersQ.data?.total ?? users.length;

  return (
    <Shell breadcrumb="auth_service / users">
      <aside className="w-[360px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2 flex flex-col gap-2">
          <div className="flex gap-1">
            <TabBtn icon={<User className="w-3 h-3" />} active={tab === "users"} onClick={() => setTab("users")}>Users · {usersCount}</TabBtn>
            <TabBtn icon={<UsersRound className="w-3 h-3" />} active={tab === "groups"} onClick={() => setTab("groups")}>Groups · {deptGroups.length}</TabBtn>
            <TabBtn icon={<Bot className="w-3 h-3" />} active={tab === "bots"} onClick={() => setTab("bots")}>Bots · {deptBots.length}</TabBtn>
          </div>
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder={`Поиск ${tab === "users" ? "пользователей" : tab === "groups" ? "групп" : "ботов"}...`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="text-[10px] text-dim text-center pt-1">
            Матрицы доступа — внутри карточки пользователя (вкладка «Матрица доступа»).
          </div>
          {usersQ.loading && <div className="spinner" aria-label="Loading" />}
          {usersQ.error && (
            <div className="alert-danger text-[11px]">{usersQ.error.message}</div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {tab === "users" && (
            <>
              <div className="group-header flex items-center gap-2">
                <User className="w-3 h-3" /> my · {myRow ? 1 : 0}
              </div>
              <div className="px-2 flex flex-col gap-0.5">
                {myRow ? (
                  <UserRowItem
                    user={myRow}
                    selected={targetUser?.id === myRow.id}
                    onClick={() => setSelectedId(myRow.id)}
                    isSelf
                  />
                ) : (
                  <div className="px-3 py-2 text-xs text-dim italic">
                    {usersQ.loading ? "…" : "—"}
                  </div>
                )}
              </div>

              <div className="group-header flex items-center gap-2 mt-3">
                <UsersRound className="w-3 h-3" /> my_dep · {deptName} · {otherRows.length}
              </div>
              <div className="px-2 flex flex-col gap-0.5">
                {otherRows.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-dim italic">
                    {usersQ.loading ? "…" : "Других пользователей в отделе нет."}
                  </div>
                ) : (
                  otherRows.map((u) => (
                    <UserRowItem
                      key={u.id}
                      user={u}
                      selected={targetUser?.id === u.id}
                      onClick={() => setSelectedId(u.id)}
                    />
                  ))
                )}
                <TruncationNotice
                  className="mx-1 mt-1"
                  shown={users.length}
                  total={usersQ.data?.total ?? null}
                />
              </div>

              <div className="group-header flex items-center gap-2 mt-3">
                <Building2 className="w-3 h-3" /> cross_dep · 0
              </div>
              <div className="px-3 py-2 text-xs text-dim italic">
                Нет доступа к другим dept&apos;ам — dep_admin видит только своих.
              </div>
            </>
          )}

          {tab === "groups" && (
            <div className="px-2 flex flex-col gap-0.5">
              <div className="group-header flex items-center gap-2">
                <UsersRound className="w-3 h-3" /> Группы депа · {deptGroups.length}
              </div>
              {groupsQ.loading ? (
                <div className="spinner mx-3" aria-label="Loading" />
              ) : groupsQ.error ? (
                <div className="alert-danger text-[11px] mx-3">{groupsQ.error.message}</div>
              ) : deptGroups.length === 0 ? (
                <div className="empty-card text-xs mx-3">Групп нет</div>
              ) : (
                deptGroups.map((g) => (
                  <Link key={g.id} to={`/users/group/${g.id}`} className="cred-row">
                    <GroupAsideRow group={g} />
                  </Link>
                ))
              )}
              <TruncationNotice
                className="mx-1 mt-1"
                shown={groupsQ.data?.items.length ?? 0}
                total={groupsQ.data?.total ?? null}
              />
            </div>
          )}

          {tab === "bots" && (
            <div className="px-2 flex flex-col gap-0.5">
              <div className="group-header flex items-center gap-2">
                <Bot className="w-3 h-3" /> Боты депа · {deptBots.length}
              </div>
              {botsQ.loading ? (
                <div className="spinner mx-3" aria-label="Loading" />
              ) : botsQ.error ? (
                <div className="alert-danger text-[11px] mx-3">{botsQ.error.message}</div>
              ) : deptBots.length === 0 ? (
                <div className="empty-card text-xs mx-3">Ботов нет</div>
              ) : (
                deptBots.map((b) => (
                  <Link key={b.id} to={`/users/bot/${b.id}`} className="cred-row">
                    <BotAsideRow bot={b} />
                  </Link>
                ))
              )}
              <TruncationNotice
                className="mx-1 mt-1"
                shown={botsQ.data?.items.length ?? 0}
                total={botsQ.data?.total ?? null}
              />
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
                    hint: "dep_admin создаёт группы только в своём dept-е",
                  }
                : tab === "bots"
                  ? {
                      icon: <Bot className="w-4 h-4" />,
                      label: "Создать бота",
                      allowed: botCaps.rotateToken,
                      reason: botCaps.reason,
                      hint: "первый токен показывается один раз — скопируйте сразу",
                    }
                  : {
                      icon: <UserPlus className="w-4 h-4" />,
                      label: "Создать пользователя",
                      allowed: groupCaps.edit,
                      reason: groupCaps.reason,
                      hint: "dep_admin создаёт пользователей и ботов только в своём отделе",
                    };
            return (
              <>
                <button
                  className="btn btn-primary w-full flex items-center justify-center gap-2"
                  disabled={!cfg.allowed}
                  title={cfg.allowed ? undefined : cfg.reason}
                  onClick={() => setCreateOpen(true)}
                >
                  {cfg.icon} {cfg.label}
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
                <span className={`badge badge-${userStatusBadgeKind(targetUser.status)}`}>
                  {normalizeUserStatus(targetUser.status)}
                </span>
              )}
              {targetUser?.platform_role && (
                <span className="badge badge-accent">{targetUser.platform_role}</span>
              )}
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="flex items-center gap-1">
                <Mail className="w-3 h-3" /> {targetUser?.email || "—"}
              </span>
              <span>·</span>
              <span className="mono">{targetUser?.id ?? "—"}</span>
              <span>·</span>
              <span>
                dept: <b>{targetUser?.dept_name ?? deptName}</b>
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
              disabled={!caps.disable || busy !== null || !targetUser || isSelfTarget}
              title={
                isSelfTarget
                  ? "нельзя заблокировать самого себя"
                  : caps.disable
                    ? undefined
                    : caps.reason
              }
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
                      v={<span className="mono text-dim">{targetUser.dept_id ?? "—"}</span>}
                    />
                  </div>
                  <div>
                    <StatRow k="created_at" v={targetUser.created_at ?? "—"} />
                    <StatRow
                      k="status"
                      v={
                        <span className={`badge badge-${userStatusBadgeKind(targetUser.status)}`}>
                          {normalizeUserStatus(targetUser.status)}
                        </span>
                      }
                    />
                    <StatRow
                      k="must_change_password"
                      v={<span className="text-dim">{targetUser.must_change_password ? "да" : "нет"}</span>}
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
                  <Shield className="w-4 h-4" /> Платформенные роли
                </div>
                <div className="text-sm">
                  {targetUser?.platform_role ? (
                    <div className="flex items-center gap-2 mb-2">
                      <span className="badge badge-accent">{targetUser.platform_role}</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 mb-2">
                      <span className="badge">user</span>
                      <span className="text-xs text-dim">
                        — обычный пользователь без платформенных прав
                      </span>
                    </div>
                  )}
                  <div className="text-xs text-dim">
                    dep_admin не может выдавать платформенные роли — это уровень account_admin.
                  </div>
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
              {sessionsQ.loading ? (
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
              <div className="empty-card text-xs">
                Admin-просмотр чужих PAT недоступен — есть только `/me/tokens`.
              </div>
            </div>
          )}

          {workzoneTab === "bots" && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <Bot className="w-4 h-4" /> Боты, созданные {tgtLabel}
              </div>
              {botsQ.loading ? (
                <div className="spinner" aria-label="Loading" />
              ) : botsQ.error ? (
                <div className="alert-danger text-[11px]">{botsQ.error.message}</div>
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
                          <span className={`badge badge-${b.status === "active" ? "ok" : "warn"}`}>
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
            mockMode={false}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              toast.success("Пользователь создан");
              usersQ.refetch();
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "groups" && (
        <Modal title="Новая группа" onClose={() => setCreateOpen(false)}>
          <CreateGroupForm
            depts={deptsLite}
            defaultDeptId={myDept}
            mockMode={false}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              toast.success("Группа создана");
              groupsQ.refetch();
              void invalidateLabels("groups");
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "bots" && (
        <Modal title="Новый бот" onClose={() => setCreateOpen(false)}>
          <CreateBotForm
            depts={deptsLite}
            defaultDeptId={myDept}
            mockMode={false}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              toast.success("Бот создан");
              botsQ.refetch();
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
            mockMode={false}
            onCancel={() => setEditRolesOpen(false)}
            onSuccess={() => {
              setEditRolesOpen(false);
              toast.success("Роли обновлены");
              usersQ.refetch();
            }}
          />
        </Modal>
      )}
    </Shell>
  );
}

function UserRowItem({
  user,
  selected,
  onClick,
  isSelf,
}: {
  user: ApiUser;
  selected: boolean;
  onClick: () => void;
  isSelf?: boolean;
}) {
  const status = normalizeUserStatus(user.status);
  return (
    <button
      onClick={onClick}
      className={`cred-row text-left ${selected ? "active" : ""}`}
    >
      <div className="flex items-center gap-2">
        {user.platform_role ? (
          <UserCog className={`w-4 h-4 ${selected ? "text-accent" : "text-warn"}`} />
        ) : (
          <User className={`w-4 h-4 ${selected ? "text-accent" : "text-dim"}`} />
        )}
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate flex items-center gap-2">
            <span>{user.username}</span>
            {user.platform_role && <span className="badge">{user.platform_role}</span>}
            {isSelf && <span className="badge badge-accent">вы</span>}
          </div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <span className="mono truncate max-w-[160px]">{user.id}</span>
          </div>
        </div>
        <span className={`badge badge-${userStatusBadgeKind(status)}`}>{status}</span>
      </div>
    </button>
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

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[160px_1fr] gap-2 py-1.5 border-b border-dashed border-token text-sm last:border-b-0">
      <span className="text-dim">{k}</span>
      <span>{v}</span>
    </div>
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
      <span className={`badge badge-${bot.status === "active" ? "ok" : "warn"}`}>
        {bot.status}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mock: статичный мокап dep_admin (для демо-персон без бэкенда)
// ---------------------------------------------------------------------------

function UsersDepAdminMock() {
  const [tab, setTab] = useState<Tab>("users");
  const [workzoneTab, setWorkzoneTab] = useState<WorkzoneTab>("profile");
  const coreGroups = GROUPS.filter((g) => g.owner_dept === "core" || g.cross_dept);
  const coreBots = BOTS.filter((b) => b.owner_dept === "core");
  const toast = useToast();
  const { persona } = usePersona();

  const targetUser = useMemo(() => {
    const m = USERS.find((u) => u.username === "dave");
    if (!m) return null;
    return {
      id: m.id,
      username: m.username,
      platform_role: null as null,
      dept_id: m.dept_id ?? "core",
    };
  }, []);

  const caps = useMemo(
    () => userMutationCaps(persona, targetUser?.dept_id ?? null),
    [persona, targetUser?.dept_id],
  );

  const myDept = useMemo(() => personaDeptId(persona), [persona]);
  const groupCaps = useMemo(() => groupMutationCaps(persona, myDept), [persona, myDept]);
  const botCaps = useMemo(() => botMutationCaps(persona, myDept), [persona, myDept]);

  const [createOpen, setCreateOpen] = useState(false);
  const [editRolesOpen, setEditRolesOpen] = useState(false);

  const tgtLabel = targetUser ? targetUser.username : "пользователь";

  return (
    <Shell breadcrumb="auth_service / users">
      <aside className="w-[360px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2 flex flex-col gap-2">
          <div className="flex gap-1">
            <TabBtn icon={<User className="w-3 h-3" />} active={tab === "users"} onClick={() => setTab("users")}>Users · 8</TabBtn>
            <TabBtn icon={<UsersRound className="w-3 h-3" />} active={tab === "groups"} onClick={() => setTab("groups")}>Groups · {coreGroups.length}</TabBtn>
            <TabBtn icon={<Bot className="w-3 h-3" />} active={tab === "bots"} onClick={() => setTab("bots")}>Bots · {coreBots.length}</TabBtn>
          </div>
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-dim" />
            <input
              className="bg-transparent outline-none flex-1 text-sm"
              placeholder={`Поиск ${tab === "users" ? "пользователей" : tab === "groups" ? "групп" : "ботов"}...`}
            />
          </div>
          <div className="text-[10px] text-dim text-center pt-1">
            Матрицы доступа — внутри карточки пользователя (вкладка «Матрица доступа»).
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {tab === "users" && (
            <>
              <div className="group-header flex items-center gap-2">
                <User className="w-3 h-3" /> my · 1
              </div>
              <div className="px-2 flex flex-col gap-0.5">
                {MY.map((r) => (
                  <RowItem key={r.name} row={r} />
                ))}
              </div>

              <div className="group-header flex items-center gap-2 mt-3">
                <UsersRound className="w-3 h-3" /> my_dep · Ядро DBOS · 7
              </div>
              <div className="px-2 flex flex-col gap-0.5">
                {MY_DEP.map((r) => (
                  <RowItem key={r.name} row={r} />
                ))}
              </div>

              <div className="group-header flex items-center gap-2 mt-3">
                <Building2 className="w-3 h-3" /> cross_dep · 0
              </div>
              <div className="px-3 py-2 text-xs text-dim italic">
                Нет доступа к другим dept&apos;ам — dep_admin видит только своих.
              </div>
            </>
          )}

          {tab === "groups" && (
            <div className="px-2 flex flex-col gap-0.5">
              <div className="group-header flex items-center gap-2">
                <UsersRound className="w-3 h-3" /> Группы депа · {coreGroups.length}
              </div>
              {coreGroups.map((g) => (
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
              ))}
            </div>
          )}

          {tab === "bots" && (
            <div className="px-2 flex flex-col gap-0.5">
              <div className="group-header flex items-center gap-2">
                <Bot className="w-3 h-3" /> Боты депа · {coreBots.length}
              </div>
              {coreBots.map((b) => (
                <Link key={b.id} to={`/users/bot/${b.id}`} className="cred-row">
                  <div className="flex items-center gap-2">
                    <Bot className="w-4 h-4 text-dim" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate mono">{b.name}</div>
                      <div className="text-[11px] text-dim">{formatMskDate(b.last_used)}</div>
                    </div>
                    <span className={`badge badge-${b.token_status === "active" ? "ok" : b.token_status === "rotated" ? "warn" : "danger"}`}>
                      {b.token_status}
                    </span>
                  </div>
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
                    hint: "dep_admin создаёт группы только в своём dept-е",
                  }
                : tab === "bots"
                  ? {
                      icon: <Bot className="w-4 h-4" />,
                      label: "Создать бота",
                      allowed: botCaps.rotateToken,
                      reason: botCaps.reason,
                      hint: "первый токен показывается один раз — скопируйте сразу",
                    }
                  : {
                      icon: <UserPlus className="w-4 h-4" />,
                      label: "Создать пользователя",
                      allowed: caps.edit,
                      reason: caps.reason,
                      hint: "dep_admin создаёт пользователей и ботов только в Ядро DBOS",
                    };
            return (
              <>
                <button
                  className="btn btn-primary w-full flex items-center justify-center gap-2"
                  disabled={!cfg.allowed}
                  title={cfg.allowed ? undefined : cfg.reason}
                  onClick={() => setCreateOpen(true)}
                >
                  {cfg.icon} {cfg.label}
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
            DA
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate">dave</h1>
              <span className="badge badge-ok">active</span>
              <span className="badge">user</span>
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="flex items-center gap-1">
                <Mail className="w-3 h-3" /> dave@dbos.local
              </span>
              <span>·</span>
              <span className="mono">usr_b4e21f08a973</span>
              <span>·</span>
              <span>
                dept: <b>Ядро DBOS</b>
              </span>
              <span>·</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" /> 1 ч назад
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.edit || !targetUser}
              title={caps.edit ? undefined : caps.reason}
              onClick={() => toast.info("mock: reset-password")}
            >
              <KeyRound className="w-4 h-4" /> Reset password
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={!caps.disable || !targetUser}
              title={caps.disable ? undefined : caps.reason}
              onClick={() => toast.info("mock: disable")}
            >
              <Pause className="w-4 h-4" /> Block
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={!caps.disable || !targetUser}
              title={caps.disable ? undefined : caps.reason}
              onClick={() => toast.info("mock: revoke-sessions")}
            >
              <LogOut className="w-4 h-4" /> Revoke sessions
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
              <div className="grid grid-cols-2 gap-x-6 text-sm">
                <div>
                  <StatRow k="username" v={<span className="mono">dave</span>} />
                  <StatRow k="email" v={<span className="mono">dave@dbos.local</span>} />
                  <StatRow k="ID" v={<span className="mono">usr_b4e21f08a973</span>} />
                  <StatRow k="dept_id" v={<span className="mono">dep_a17c · Ядро DBOS</span>} />
                </div>
                <div>
                  <StatRow k="created_at" v="2026-01-22 14:48" />
                  <StatRow k="created_by" v={<span className="mono">alice</span>} />
                  <StatRow k="status" v={<span className="badge badge-ok">active</span>} />
                  <StatRow k="must_change_password" v={<span className="text-dim">нет</span>} />
                </div>
              </div>
            </div>
          )}

          {workzoneTab === "roles" && (
            <>
              <div className="surface border border-token rounded-lg p-4">
                <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                  <Shield className="w-4 h-4" /> Платформенные роли
                </div>
                <div className="text-sm">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="badge">user</span>
                    <span className="text-xs text-dim">
                      — обычный пользователь без платформенных прав
                    </span>
                  </div>
                  <div className="text-xs text-dim">
                    dep_admin не может выдавать платформенные роли — это уровень account_admin.
                  </div>
                </div>
              </div>

              <div className="surface border border-token rounded-lg p-4">
                <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                  <Cog className="w-4 h-4" /> Service-роли
                </div>
                <div className="text-sm">
                  <div className="flex items-center justify-between py-1.5 border-b border-dashed border-token">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-dim" />
                      <span className="mono text-xs">loging</span>
                      <span className="text-xs text-dim">·</span>
                      <span className="text-xs">Ядро DBOS</span>
                    </div>
                    <span className="badge badge-accent">logging-reader</span>
                  </div>
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
                    <td className="py-2 mono">10.177.103.58</td>
                    <td className="flex items-center gap-1 py-2">
                      <Chrome className="w-3 h-3" /> Linux · Chrome 132
                    </td>
                    <td className="text-dim text-xs">1 ч назад</td>
                    <td>
                      <span className="text-ok">активна</span>
                    </td>
                    <td>
                      <button
                        className="btn btn-danger text-xs"
                        disabled={!caps.disable || !targetUser}
                        title={caps.disable ? undefined : caps.reason}
                        onClick={() => toast.info("mock: revoke-session")}
                      >
                        revoke
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {workzoneTab === "pats" && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <KeyRound className="w-4 h-4" /> Активные PATs
              </div>
              <div className="empty-card text-xs">
                Admin-просмотр чужих PAT недоступен — есть только `/me/tokens`.
              </div>
            </div>
          )}

          {workzoneTab === "bots" && (
            <div className="surface border border-token rounded-lg p-4 col-span-2">
              <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
                <Bot className="w-4 h-4" /> Боты, созданные {tgtLabel}
              </div>
              <div className="text-sm text-dim italic">
                mock: список ботов будет подтянут из API в live-mode.
              </div>
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
            depts={[]}
            mockMode
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              toast.info("mock: create user");
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "groups" && (
        <Modal title="Новая группа" onClose={() => setCreateOpen(false)}>
          <CreateGroupForm
            depts={[]}
            defaultDeptId={myDept}
            mockMode
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              toast.info("mock: create group");
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "bots" && (
        <Modal title="Новый бот" onClose={() => setCreateOpen(false)}>
          <CreateBotForm
            depts={[]}
            defaultDeptId={myDept}
            mockMode
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              toast.info("mock: create bot");
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
            depts={[]}
            mockMode
            onCancel={() => setEditRolesOpen(false)}
            onSuccess={() => {
              setEditRolesOpen(false);
              toast.success("Роли обновлены");
            }}
          />
        </Modal>
      )}
    </Shell>
  );
}

function RowItem({ row }: { row: Row }) {
  const cogColorClass =
    row.cogColor === "warn"
      ? "text-warn"
      : row.cogColor === "accent"
      ? "text-accent"
      : row.active
      ? "text-accent"
      : "text-dim";
  const real = USERS.find((u) => u.username === row.name);
  const linkTo = row.isBot
    ? `/users/bot/${row.name.startsWith("bot-") ? row.name.replace("bot-", "b-") : row.name}`
    : real
      ? `/users/${real.id}`
      : `/users/${row.name}`;
  return (
    <Link to={linkTo} className={`cred-row ${row.active ? "active" : ""}`}>
      <div className="flex items-center gap-2">
        {row.isBot ? (
          <Bot className="w-4 h-4 text-dim" />
        ) : row.isCog ? (
          <UserCog className={`w-4 h-4 ${cogColorClass}`} />
        ) : (
          <User className={`w-4 h-4 ${row.active ? "text-accent" : "text-dim"}`} />
        )}
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate flex items-center gap-2">
            <span className={row.isBot ? "mono" : ""}>{row.name}</span>
            {row.role && <span className="badge">{row.role.label}</span>}
          </div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <span>{row.dept}</span>
            <span>·</span>
            <span>{row.lastSeen}</span>
          </div>
        </div>
        <span className={`badge badge-${row.status.kind}`}>{row.status.label}</span>
      </div>
    </Link>
  );
}
