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
  listUserSessions,
  resetUserPassword,
  revokeUserSessionById,
  revokeUserSessions,
} from "@/api/auth/users";
import { listBots } from "@/api/auth/bots";
import { listDepartments } from "@/api/auth/departments";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { useDeptLabel } from "@/lib/labels";
import { userMutationCaps, groupMutationCaps, botMutationCaps, personaDeptId } from "@/lib/rbac";
import type { Department } from "@/api/auth/types";
import {
  CreateBotForm,
  CreateGroupForm,
  CreateUserForm,
  EditRolesForm,
  Modal,
} from "./_userActions";

/**
 * Port of users-dep_admin.html (alice).
 * Middle: users grouped my / my_dep (Ядро DBOS) / cross_dep (empty).
 * Workzone: dave profile.
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
  const [tab, setTab] = useState<Tab>("users");
  const [workzoneTab, setWorkzoneTab] = useState<WorkzoneTab>("profile");
  const coreGroups = GROUPS.filter((g) => g.owner_dept === "core" || g.cross_dept);
  const coreBots = BOTS.filter((b) => b.owner_dept === "core");
  const mockMode = useMockMode();
  const toast = useToast();
  const { persona } = usePersona();

  const deptsQ = useQuery<Department[]>(
    () => listDepartments(),
    [],
    { enabled: !mockMode },
  );
  const deptsLite = useMemo(() => {
    if (mockMode) return [];
    return (deptsQ.data ?? []).map((d) => ({
      id: d.id,
      name: d.name,
      display_name: d.display_name,
    }));
  }, [mockMode, deptsQ.data]);

  // Workzone target: dave (Ядро DBOS). dep_admin caps depend on matching
  // target dept_id, so we hard-code "core" here to match the mock view.
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

  const sessionsQ = useQuery(
    () => listUserSessions(targetUser!.id),
    [targetUser?.id, workzoneTab],
    { enabled: !mockMode && !!targetUser && workzoneTab === "sessions" },
  );

  const botsQ = useQuery(
    () => listBots({ limit: 200 }),
    [targetUser?.id, workzoneTab],
    { enabled: !mockMode && !!targetUser && workzoneTab === "bots" },
  );
  const botsCreatedByTarget = useMemo(() => {
    if (!targetUser) return [];
    return (botsQ.data ?? []).filter((b) => b.created_by === targetUser.id);
  }, [botsQ.data, targetUser]);

  // dep_admin only creates groups/bots inside own dept.
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
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e);
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
    if (!targetUser) return;
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

  // Real-mode placeholder: this view was a verbatim mockup port (hard-coded
  // dept-user rows plus a static profile workzone). For real-mode
  // dep_admin browsing we redirect users to the live `ServicesUsers` view —
  // /admin/services.auth.users — which already wires the same actions to the
  // API and filters by dept.
  if (!mockMode) {
    return (
      <Shell breadcrumb="auth_service / users">
        <section className="flex-1 overflow-y-auto p-8">
          <div className="empty-card max-w-xl">
            <div className="text-sm font-medium mb-2">
              Live-режим: используйте Admin · auth_service · Пользователи
            </div>
            <div className="text-xs text-dim mb-3">
              Эта страница — порт мокапа dep_admin (статичные имена и счётчики).
              Полный CRUD по реальным API живёт в админ-хабе.
            </div>
            <Link
              to="/admin/services.auth.users"
              className="btn btn-primary inline-flex items-center gap-2"
            >
              Перейти в Admin · Users
            </Link>
          </div>
        </section>
      </Shell>
    );
  }

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
                      <div className="text-[11px] text-dim">{b.last_used.slice(0, 10)}</div>
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
                  <StatRow
                    k="dept_id"
                    v={<span className="mono">dep_a17c · Ядро DBOS</span>}
                  />
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
                          onClick={() => handleSessionRevoke("ses_dep_demo")}
                        >
                          revoke
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
                        <td className="text-dim text-xs">{s.created_at.slice(0, 16)}</td>
                        <td className="text-dim text-xs">
                          {s.last_used_at ? s.last_used_at.slice(0, 16) : "—"}
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
              {mockMode ? (
                <div className="text-sm text-dim italic">
                  mock: список ботов будет подтянут из API в live-mode.
                </div>
              ) : botsQ.loading ? (
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
                          <span
                            className={`badge badge-${b.status === "active" ? "ok" : "warn"}`}
                          >
                            {b.status}
                          </span>
                        </td>
                        <td className="text-dim text-xs">{b.created_at.slice(0, 10)}</td>
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
              if (mockMode) toast.info("mock: create user");
              else toast.success("Пользователь создан");
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "groups" && (
        <Modal title="Новая группа" onClose={() => setCreateOpen(false)}>
          <CreateGroupForm
            depts={deptsLite}
            defaultDeptId={myDept}
            mockMode={mockMode}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              if (mockMode) toast.info("mock: create group");
              else toast.success("Группа создана");
            }}
          />
        </Modal>
      )}

      {createOpen && tab === "bots" && (
        <Modal title="Новый бот" onClose={() => setCreateOpen(false)}>
          <CreateBotForm
            depts={deptsLite}
            defaultDeptId={myDept}
            mockMode={mockMode}
            onCancel={() => setCreateOpen(false)}
            onSuccess={() => {
              setCreateOpen(false);
              if (mockMode) toast.info("mock: create bot");
              else toast.success("Бот создан");
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
