import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  User as UserIcon,
  ShieldCheck,
  Cog,
  UsersRound,
  KeyRound,
  Pause,
  Play,
  Unlock,
  LogOut,
  Mail,
  Clock,
  GitCompareArrows,
  ChevronRight,
  ChevronDown,
  Filter,
  ListTree,
  Activity,
  Trash2,
  Grid2x2,
  XCircle,
  ShieldOff,
  Monitor,
  Plus,
  Pencil,
  X,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Tabs } from "@/components/ui/Tabs";
import { USERS, userById, DEPTS } from "@/mocks/auth";
import { usePersona } from "@/contexts/PersonaContext";
import { userMutationCaps } from "@/lib/rbac";
import { UserAccessMatrices } from "./AccessMatrix";
import {
  assignUserRoles,
  banUser,
  deleteUser,
  disableUser,
  enableUser,
  forcePasswordChange,
  getUser,
  getUserGroups,
  getUserPermissions,
  listUserSessions,
  normalizeUserStatus,
  resetUserPassword,
  revokeUserAllSessions,
  revokeUserSessionById,
  revokeUserSessions,
  unbanUser,
  unlockUser,
} from "@/api/auth/users";
import {
  addUserToGroup,
  listGroupsWithTotal,
  removeUserFromGroup,
} from "@/api/auth/groups";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { listServices } from "@/api/auth/services";
import { listServiceRoles } from "@/api/auth/service_roles";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import { useDeptLabel, useLabelMaps, useServiceLabel } from "@/lib/labels";
import { formatMsk, formatMskDate, formatMskShort } from "@/lib/datetime";
import type {
  Group as ApiGroup,
  Service as ApiService,
  ServiceName,
  ServiceRole as ApiServiceRole,
  SessionListResponse,
} from "@/api/auth/types";
import {
  GROUPS,
  ROLES,
  USER_ASSIGNMENTS,
  GROUP_ASSIGNMENTS,
  DIRECT_GRANTS,
  auditForUser,
} from "@/mocks/permissions";
import {
  computeEffectiveUser,
  applyMutation,
  computeDiff,
  defaultStore,
  traceForUser,
  type EffectiveEntry,
  type DiffEntry,
  type Mutation,
  type TraceNode,
} from "./permissionGraph";

interface SectionProps {
  icon: React.ReactNode;
  title: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

function Section({ icon, title, children, className = "" }: SectionProps) {
  return (
    <div className={`surface border border-token rounded-lg p-4 flex flex-col min-h-0 ${className}`}>
      <div className="text-xs uppercase tracking-wider text-dim mb-3 flex items-center gap-2">
        {icon} {title}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  );
}

export function UserDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { persona } = usePersona();
  const mockMode = useMockMode();

  // API-backed user (skip in mock mode).
  const apiUserQ = useQuery(
    () => getUser(id ?? ""),
    [id],
    { enabled: !mockMode && !!id },
  );
  // Permissions + groups: nice-to-have, surfaced in the new sidebar block.
  const permsQ = useQuery(
    () => getUserPermissions(id ?? ""),
    [id],
    { enabled: !mockMode && !!id },
  );
  const groupsApiQ = useQuery(
    () => getUserGroups(id ?? ""),
    [id],
    { enabled: !mockMode && !!id },
  );

  // В mock-режиме источник — статичный USERS; в live-режиме — apiUserQ.
  // В mock-моде с разрешения id-by-username для удобства, в live этого делать
  // не надо (apiUserQ ходит по id из роута).
  const user = useMemo(() => {
    if (!id) return undefined;
    if (mockMode) {
      return userById(id) ?? USERS.find((u) => u.username === id);
    }
    if (apiUserQ.data) {
      const u = apiUserQ.data;
      return {
        id: u.id,
        username: u.username,
        email: u.email ?? "",
        dept_id: u.department_id,
        platform_role: u.platform_role ?? null,
        status: normalizeUserStatus(u.status) as
          | "active"
          | "blocked"
          | "pending",
        last_login: u.updated_at ?? u.created_at,
        is_bot: false,
        created_by: null,
        mfa_enabled: false,
      };
    }
    return undefined;
  }, [id, mockMode, apiUserQ.data]);

  const [actionErr, setActionErr] = useState<string | null>(null);
  const [actionInfo, setActionInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Бампается после любого header/danger-действия (ban/disable/revoke и т.д.),
  // чтобы вкладка сессий перечитала список — ban и revoke инвалидируют сессии
  // на backend, иначе вкладка показывает «живые» сессии, которых уже нет.
  const [actionSignal, setActionSignal] = useState(0);
  async function runAction(label: string, fn: () => Promise<unknown>) {
    if (mockMode) {
      setActionInfo(`mock: ${label}`);
      setActionErr(null);
      return;
    }
    setBusy(label);
    setActionErr(null);
    setActionInfo(null);
    try {
      await fn();
      setActionInfo(`${label}: OK`);
      apiUserQ.refetch();
      setActionSignal((n) => n + 1);
    } catch (e) {
      setActionErr(
        apiErrMsg(e),
      );
    } finally {
      setBusy(null);
    }
  }

  const caps = useMemo(
    () => userMutationCaps(persona, user?.dept_id ?? null),
    [persona, user?.dept_id],
  );

  const [filterSvc, setFilterSvc] = useState<string>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [diffMutation, setDiffMutation] = useState<Mutation | null>(null);
  const [tab, setTab] = useState<"profile" | "matrix" | "sessions">("profile");
  const [showAddGroup, setShowAddGroup] = useState(false);
  const [showEditRoles, setShowEditRoles] = useState(false);

  const userId = user?.id ?? "";
  const effective = useMemo(
    () => (userId ? computeEffectiveUser(userId) : []),
    [userId],
  );

  const services = useMemo(() => {
    const set = new Set(effective.map((e) => e.service));
    return ["all", ...Array.from(set)];
  }, [effective]);

  const filteredEff = useMemo(() => {
    return filterSvc === "all" ? effective : effective.filter((e) => e.service === filterSvc);
  }, [effective, filterSvc]);

  const diff = useMemo<DiffEntry[] | null>(() => {
    if (!diffMutation || !userId) return null;
    const afterStore = applyMutation(defaultStore, diffMutation);
    const after = computeEffectiveUser(userId, afterStore);
    return computeDiff(effective, after);
  }, [diffMutation, effective, userId]);

  if (!user) {
    return (
      <Shell breadcrumb="auth_service / users">
        <section className="flex-1 overflow-y-auto p-8">
          <div className="empty-card danger">
            Пользователь <span className="mono">{id}</span> не найден.
            <div className="mt-3">
              <Link to="/users" className="btn">
                <ArrowLeft className="w-4 h-4 inline mr-1" /> Вернуться к списку
              </Link>
            </div>
          </div>
        </section>
      </Shell>
    );
  }

  // В mock-режиме оставляем богатые mock-данные (dept по id, permission-graph
  // assignments, ссылки на mock-группы). В live-режиме шапка/dept ходят через
  // useDeptLabel, а Platform roles / Direct grants / Effective / Audit /
  // permission-graph секции скрываются (для них пока нет endpoint'ов).
  const dept = mockMode && user.dept_id
    ? DEPTS.find((d) => d.id === user.dept_id)
    : null;
  const assignments = mockMode ? USER_ASSIGNMENTS[user.id] : undefined;
  const groups = mockMode
    ? ((user.groups ?? assignments?.groups ?? []).map((gid) =>
        GROUPS.find((g) => g.id === gid),
      ).filter(Boolean) as typeof GROUPS)
    : [];

  const toggleExpand = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const initials = user.username.slice(0, 2).toUpperCase();
  // MFA is not implemented in auth_service — column does not exist on User.
  const audit = auditForUser(user.id, user.dept_id);

  return (
    <Shell breadcrumb={`auth_service / users / ${user.username}`}>
      <section className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* HEADER */}
        <div className="border-b border-token p-5 flex items-start gap-4 shrink-0">
          <Link to="/users" className="btn btn-ghost mt-1">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="w-12 h-12 rounded-full bg-accent flex items-center justify-center text-base font-semibold">
            {initials}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold truncate">{user.username}</h1>
              <span className={`badge badge-${user.status === "active" ? "ok" : user.status === "blocked" ? "warn" : "danger"}`}>
                {user.status}
              </span>
              {user.platform_role && (
                <span className="badge badge-accent">{user.platform_role}</span>
              )}
            </div>
            <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
              <span className="flex items-center gap-1">
                <Mail className="w-3 h-3" /> {user.email}
              </span>
              <span>·</span>
              <span className="mono">{user.id}</span>
              <span>·</span>
              <span>dept: <b>{mockMode ? (dept?.name ?? "— (платформенный)") : (user.dept_id ? <HeaderDeptName deptId={user.dept_id} /> : "— (платформенный)")}</b></span>
              <span>·</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" /> {formatMskShort(user.last_login)}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.edit || busy !== null}
              title={caps.edit ? undefined : caps.reason}
              onClick={() => {
                const pwd = window.prompt("Новый пароль (min 12, буквы + цифры):");
                if (!pwd) return;
                runAction("reset-password", () =>
                  resetUserPassword(user.id, { new_password: pwd }),
                );
              }}
            >
              <KeyRound className="w-4 h-4" /> Reset password
            </button>
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.edit || busy !== null}
              title={
                caps.edit
                  ? "Принудить юзера сменить пароль на ближайшем логине"
                  : caps.reason
              }
              onClick={() => {
                // Confirm: side-effect — на следующем запросе у target'а
                // полетит 403 PASSWORD_CHANGE_REQUIRED везде, кроме
                // `/users/me/password`. Дороже отката, чем reset-password,
                // — пароль не меняем, но саму ручку не идемпотентным
                // unset-ом не открутить.
                if (
                  !window.confirm(
                    `Принудить ${user.username} сменить пароль при ближайшем входе?\n` +
                      "Текущий пароль не меняется, но юзер не сможет работать с системой до self-reset'а через /users/me/password.",
                  )
                ) {
                  return;
                }
                runAction("force-pwd-change", () => forcePasswordChange(user.id));
              }}
            >
              <KeyRound className="w-4 h-4" /> Force pwd change
            </button>
            {user.status === "active" ? (
              <button
                className="btn btn-danger flex items-center gap-1"
                disabled={!caps.disable || busy !== null}
                title={caps.disable ? undefined : caps.reason}
                onClick={() => runAction("disable", () => disableUser(user.id))}
              >
                <Pause className="w-4 h-4" /> Block
              </button>
            ) : (
              <button
                className="btn flex items-center gap-1"
                disabled={!caps.disable || busy !== null}
                title={caps.disable ? undefined : caps.reason}
                onClick={() => runAction("enable", () => enableUser(user.id))}
              >
                <Play className="w-4 h-4" /> Unblock
              </button>
            )}
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.disable || busy !== null}
              title={
                caps.disable
                  ? "Снять lockout по неудачным попыткам логина"
                  : caps.reason
              }
              onClick={() => runAction("unlock", () => unlockUser(user.id))}
            >
              <Unlock className="w-4 h-4" /> Reset lockout
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={!caps.disable || busy !== null}
              title={caps.disable ? undefined : caps.reason}
              onClick={() =>
                runAction("revoke-sessions", () => revokeUserSessions(user.id))
              }
            >
              <LogOut className="w-4 h-4" /> Revoke sessions
            </button>
            {!caps.edit && !caps.disable && !caps.delete && (
              <span className="badge badge-warn" title={caps.reason}>
                read-only
              </span>
            )}
          </div>
        </div>

        {/* TABS */}
        <Tabs
          active={tab}
          onChange={(id) => setTab(id as "profile" | "matrix" | "sessions")}
          tabs={[
            {
              id: "profile",
              label: "Профиль и права",
              icon: <UserIcon className="w-3 h-3" />,
            },
            {
              id: "matrix",
              label: "Матрица доступа",
              icon: <Grid2x2 className="w-3 h-3" />,
            },
            {
              id: "sessions",
              label: "Сессии",
              icon: <Monitor className="w-3 h-3" />,
            },
          ]}
        />

        {/* BODY — scroll only inside selected tab */}
        {tab === "matrix" && (
          mockMode ? (
            <UserAccessMatrices userId={user.id} />
          ) : (
            <div className="flex-1 overflow-y-auto p-5">
              <div className="empty-card">
                Скоро. Матрицы доступа (user/bot × resource, role × service)
                требуют сводных endpoint'ов в auth_service — пока не реализованы.
              </div>
            </div>
          )
        )}
        {tab === "sessions" && (
          <UserSessionsTab
            userId={user.id}
            mockMode={mockMode}
            canRevoke={caps.disable}
            canRevokeReason={caps.reason}
            refreshSignal={actionSignal}
          />
        )}
        {tab === "profile" && (
        <div className="flex-1 overflow-y-auto p-5 grid grid-cols-2 gap-5 auto-rows-min">
          {/* 1. Identification */}
          <Section icon={<UserIcon className="w-4 h-4" />} title="Идентификация" className="col-span-2">
            <div className="grid grid-cols-2 gap-x-6 text-sm">
              <div>
                <StatRow k="login" v={<span className="mono">{user.username}</span>} />
                <StatRow k="email" v={<span className="mono">{user.email}</span>} />
                <StatRow k="ID" v={<span className="mono">{user.id}</span>} />
                <StatRow k="dept" v={mockMode ? <span>{dept?.name ?? "— (платформенный)"}</span> : (user.dept_id ? <HeaderDeptName deptId={user.dept_id} /> : <span>— (платформенный)</span>)} />
              </div>
              <div>
                <StatRow k="status" v={<span className={`badge badge-${user.status === "active" ? "ok" : user.status === "blocked" ? "warn" : "danger"}`}>{user.status}</span>} />
                <StatRow k="created_by" v={(() => {
                  if (!user.created_by) return <span className="mono">system</span>;
                  const cb = userById(user.created_by);
                  if (cb) return <Link to={`/users/${cb.id}`} className="mono hover-bg">{cb.username}</Link>;
                  return <span className="mono text-dim" title="user removed or unknown">{user.created_by} (удалён)</span>;
                })()} />
                <StatRow k="last_login" v={<span className="mono">{formatMsk(user.last_login)}</span>} />
              </div>
            </div>
          </Section>

          {/* 2a. Sources — Groups */}
          {mockMode ? (
            <Section icon={<UsersRound className="w-4 h-4" />} title={`Членство в группах · ${groups.length}`}>
              {groups.length === 0 ? (
                <div className="text-sm text-dim italic">Не состоит ни в одной группе.</div>
              ) : (
                <div className="flex flex-col gap-2">
                  {groups.map((g) => (
                    <Link
                      key={g.id}
                      to={`/users/group/${g.id}`}
                      className="row-line hover-bg rounded px-2 -mx-2"
                      style={{ display: "flex", alignItems: "center", gap: 8 }}
                    >
                      <span className="font-medium text-sm">{g.name}</span>
                      {g.cross_dept ? (
                        <span className="badge badge-warn">cross-dept</span>
                      ) : (
                        <span className="badge">dept · {g.owner_dept}</span>
                      )}
                      <span className="text-xs text-dim ml-auto truncate max-w-[180px]" title={g.description}>
                        {g.description}
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </Section>
          ) : (
            <UserGroupsLiveSection
              userId={user.id}
              apiGroups={groupsApiQ.data ?? null}
              loading={groupsApiQ.loading}
              error={groupsApiQ.error}
              canManage={caps.manageRoles}
              capsReason={caps.reason}
              onChanged={() => groupsApiQ.refetch()}
              onOpenAdd={() => setShowAddGroup(true)}
              onActionError={(msg) => {
                setActionErr(msg);
                setActionInfo(null);
              }}
              onActionInfo={(msg) => {
                setActionInfo(msg);
                setActionErr(null);
              }}
            />
          )}

          {/* 2b. Platform roles */}
          <Section icon={<ShieldCheck className="w-4 h-4" />} title="Платформенные роли">
            {mockMode ? (
              (() => {
                const platformAssigns = (assignments?.roles ?? []).filter((ra) => {
                  const r = ROLES.find((x) => x.id === ra.role_id);
                  return r && r.service === "platform";
                });
                if (platformAssigns.length === 0) {
                  return <div className="text-sm text-dim italic">Платформенные роли не выданы.</div>;
                }
                return (
                  <div className="flex flex-col gap-2">
                    {platformAssigns.map((ra) => {
                      const role = ROLES.find((r) => r.id === ra.role_id)!;
                      return (
                        <div key={ra.role_id} className="row-line">
                          <div className="flex flex-col">
                            <span className="badge badge-accent w-fit">{role.name}</span>
                            <span className="text-xs text-dim mt-1">{role.description}</span>
                          </div>
                          <div className="text-xs text-dim text-right">
                            <div>granted by <GrantedBy id={ra.granted_by} /></div>
                            <div>{formatMskDate(ra.granted_at)}</div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              })()
            ) : user.platform_role ? (
              <div className="flex flex-col gap-1">
                <span className="badge badge-accent w-fit">{user.platform_role}</span>
                <span className="text-xs text-dim">
                  Платформенная роль из /me/permissions. Подробной истории grant'а
                  пока нет (нужен endpoint в auth_service).
                </span>
              </div>
            ) : (
              <div className="text-sm text-dim italic">Платформенные роли не выданы.</div>
            )}
          </Section>

          {/* 2c. Service roles */}
          <Section
            icon={<Cog className="w-4 h-4" />}
            title={
              <span className="flex items-center gap-2 w-full">
                <span>Service-роли</span>
                {!mockMode && (
                  <button
                    className="btn btn-sm flex items-center gap-1 ml-auto"
                    disabled={!caps.manageRoles || (!showEditRoles && !permsQ.data)}
                    title={
                      !caps.manageRoles
                        ? caps.reason
                        : !permsQ.data
                          ? "Текущие роли ещё загружаются"
                          : "Открыть форму редактирования ролей по сервисам"
                    }
                    onClick={() => setShowEditRoles((v) => !v)}
                  >
                    <Pencil className="w-3 h-3" />{" "}
                    {showEditRoles ? "Скрыть форму" : "Изменить роли"}
                  </button>
                )}
              </span>
            }
            className="col-span-2"
          >
            {!mockMode && showEditRoles && caps.manageRoles && permsQ.data && (
              <UserServiceRolesEditor
                key={user.id}
                userId={user.id}
                deptId={user.dept_id}
                currentRoles={permsQ.data?.service_roles ?? {}}
                onClose={() => setShowEditRoles(false)}
                onSaved={() => {
                  permsQ.refetch();
                  setActionInfo("Роли обновлены");
                  setActionErr(null);
                }}
                onError={(msg) => {
                  setActionErr(msg);
                  setActionInfo(null);
                }}
              />
            )}
            {!mockMode && !showEditRoles && (
              <UserServiceRolesLiveTable
                roles={permsQ.data?.service_roles ?? {}}
                loading={permsQ.loading}
                error={permsQ.error}
              />
            )}
            {mockMode && (() => {
              const svcAssigns = (assignments?.roles ?? []).filter((ra) => {
                const r = ROLES.find((x) => x.id === ra.role_id);
                return r && r.service !== "platform";
              });
              if (svcAssigns.length === 0) {
                return <div className="text-sm text-dim italic">Сервис-роли не выданы.</div>;
              }
              return (
                <table className="w-full text-sm">
                  <thead className="text-left text-dim text-xs uppercase">
                    <tr>
                      <th className="pb-2 pr-3">Сервис</th>
                      <th className="pb-2 pr-3">Роль</th>
                      <th className="pb-2 pr-3">Scope</th>
                      <th className="pb-2 pr-3">Granted by</th>
                      <th className="pb-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {svcAssigns.map((ra) => {
                      const role = ROLES.find((r) => r.id === ra.role_id)!;
                      return (
                        <tr key={ra.role_id} className="border-t border-token">
                          <td className="py-2"><span className="mono">{role.service}</span></td>
                          <td><span className="badge badge-accent">{role.name}</span></td>
                          <td className="text-xs">
                            {ra.scope_kind === "platform" && <span className="badge">платформа</span>}
                            {ra.scope_kind === "dept" && <span className="badge">отдел · {ra.scope_ref}</span>}
                            {ra.scope_kind === "resource" && <span className="badge">{ra.scope_ref}</span>}
                          </td>
                          <td className="text-xs text-dim"><GrantedBy id={ra.granted_by} /></td>
                          <td>
                            <button
                              className="btn btn-sm btn-ghost"
                              onClick={() => setDiffMutation({ kind: "remove_role", user_id: user.id, role_id: ra.role_id })}
                              title="Прикинуть: что изменится без этой роли"
                            >
                              <GitCompareArrows className="w-3 h-3" />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              );
            })()}
          </Section>

          {/* 2d. Direct grants */}
          <Section icon={<KeyRound className="w-4 h-4" />} title="Прямые grants" className="col-span-2">
            {!mockMode ? (
              <div className="text-sm text-dim italic">
                Не реализовано — auth_service не выдаёт direct grants на
                user-уровне (есть только роли и группы). Нужен отдельный
                endpoint GET /users/{"{id}"}/grants.
              </div>
            ) : (() => {
              const grants = DIRECT_GRANTS.filter((g) => g.subject_kind === "user" && g.subject_id === user.id);
              if (grants.length === 0) {
                return <div className="text-sm text-dim italic">Direct grants отсутствуют.</div>;
              }
              return (
                <table className="w-full text-sm">
                  <thead className="text-left text-dim text-xs uppercase">
                    <tr>
                      <th className="pb-2 pr-3">Permission</th>
                      <th className="pb-2 pr-3">Resource</th>
                      <th className="pb-2 pr-3">Granted by</th>
                      <th className="pb-2 pr-3">When</th>
                      <th className="pb-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {grants.map((g) => {
                      const isPreviewed =
                        diffMutation?.kind === "remove_grant" &&
                        diffMutation.grant_id === g.id;
                      return (
                        <tr key={g.id} className="border-t border-token">
                          <td className="py-2 mono text-xs">{g.permission}</td>
                          <td className="text-xs">
                            <span className={`domain-tag ${g.resource_kind === "secret" ? "tag-secret" : "tag-server"}`}>
                              {g.resource_kind}
                            </span>
                            <span className="mono">{g.resource_id}</span>
                          </td>
                          <td className="text-xs text-dim"><GrantedBy id={g.granted_by} /></td>
                          <td className="text-xs text-dim">{formatMskDate(g.granted_at)}</td>
                          <td className="text-right">
                            <button
                              className="btn btn-sm btn-ghost"
                              disabled={!caps.manageRoles}
                              title={
                                caps.manageRoles
                                  ? "Прикинуть, что потеряется без этого grant'а"
                                  : caps.reason
                              }
                              onClick={() =>
                                setDiffMutation({
                                  kind: "remove_grant",
                                  grant_id: g.id,
                                })
                              }
                            >
                              <GitCompareArrows className="w-3 h-3" /> diff
                            </button>
                            <button
                              className={`btn btn-sm ${isPreviewed ? "btn-danger" : "btn-ghost"} ml-1`}
                              disabled={!caps.manageRoles}
                              title={
                                caps.manageRoles
                                  ? isPreviewed
                                    ? "Подтвердить revoke (mock — лог в консоль)"
                                    : "Revoke direct grant"
                                  : caps.reason
                              }
                              onClick={() => {
                                if (!caps.manageRoles) return;
                                if (isPreviewed) {
                                  // auth_service не реализует
                                  // DELETE /users/{id}/grants/{grant_id}
                                  // — direct grants управляются исключительно
                                  // ролями и группами. Показываем notice вместо
                                  // фейкового подтверждения.
                                  setActionErr(null);
                                  setActionInfo(
                                    "Revoke прямого grant ещё не реализован в auth_service " +
                                      "(нет endpoint DELETE /users/{id}/grants/{grant_id}). " +
                                      "Сейчас direct grants можно поменять только переназначив роли/группы.",
                                  );
                                  setDiffMutation(null);
                                } else {
                                  setDiffMutation({
                                    kind: "remove_grant",
                                    grant_id: g.id,
                                  });
                                }
                              }}
                            >
                              <XCircle className="w-3 h-3" />{" "}
                              {isPreviewed ? "Confirm revoke" : "Revoke"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              );
            })()}
          </Section>

          {/* 3. Effective permissions (mock-only — permission graph is mock-built) */}
          {mockMode && (
          <Section icon={<ListTree className="w-4 h-4" />} title={`Effective permissions · ${effective.length}`} className="col-span-2">
            <div className="flex items-center gap-2 mb-3 text-xs">
              <Filter className="w-3 h-3 text-dim" />
              <span className="text-dim">Сервис:</span>
              <select
                className="surface-2 border border-token rounded px-2 py-0.5"
                value={filterSvc}
                onChange={(e) => setFilterSvc(e.target.value)}
              >
                {services.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <span className="text-dim ml-auto">{filteredEff.length} / {effective.length}</span>
            </div>
            <div className="border border-token rounded overflow-hidden">
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase surface-2">
                  <tr>
                    <th className="px-3 py-2 w-8"></th>
                    <th className="px-3 py-2">Permission</th>
                    <th className="px-3 py-2">Service</th>
                    <th className="px-3 py-2">Scope</th>
                    <th className="px-3 py-2">Источники</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEff.map((e) => {
                    const key = `${e.permission}|${e.scope_ref ?? ""}`;
                    const open = expanded.has(key);
                    return (
                      <PermissionRow
                        key={key}
                        entry={e}
                        userId={user.id}
                        open={open}
                        onToggle={() => toggleExpand(key)}
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Section>
          )}

          {/* 5. Diff mode (mock-only — permission graph is mock-built) */}
          {mockMode && (
          <Section icon={<GitCompareArrows className="w-4 h-4" />} title="Diff: что изменится если…" className="col-span-2">
            <div className="flex items-center gap-2 mb-3 flex-wrap text-xs">
              <span className="text-dim">Сценарий:</span>
              {assignments?.roles.map((ra) => {
                const role = ROLES.find((r) => r.id === ra.role_id)!;
                return (
                  <button
                    key={`rm-r-${ra.role_id}`}
                    className="btn btn-sm"
                    onClick={() => setDiffMutation({ kind: "remove_role", user_id: user.id, role_id: ra.role_id })}
                  >
                    − убрать роль {role.name}
                  </button>
                );
              })}
              {(assignments?.groups ?? []).map((gid) => {
                const g = GROUPS.find((x) => x.id === gid);
                if (!g) return null;
                return (
                  <button
                    key={`rm-g-${gid}`}
                    className="btn btn-sm"
                    onClick={() => setDiffMutation({ kind: "remove_from_group", user_id: user.id, group_id: gid })}
                  >
                    − убрать из {g.name}
                  </button>
                );
              })}
              {GROUPS.filter((g) => !(assignments?.groups ?? []).includes(g.id)).slice(0, 3).map((g) => (
                <button
                  key={`add-g-${g.id}`}
                  className="btn btn-sm"
                  onClick={() => setDiffMutation({ kind: "add_to_group", user_id: user.id, group_id: g.id })}
                >
                  + добавить в {g.name}
                </button>
              ))}
              {diffMutation && (
                <button className="btn btn-sm btn-ghost ml-auto" onClick={() => setDiffMutation(null)}>
                  сбросить
                </button>
              )}
            </div>
            {!diff ? (
              <div className="text-sm text-dim italic">
                Выбери сценарий — увидишь добавленные (+) и убранные (−) permission'ы.
              </div>
            ) : (
              <DiffView diff={diff} />
            )}
          </Section>
          )}

          {/* 6. Audit streak (mock-only until loging_service connected) */}
          {mockMode && (
          <Section icon={<Activity className="w-4 h-4" />} title="Последние действия (10)" className="col-span-2">
            {audit.length === 0 ? (
              <div className="text-sm text-dim italic">Аудит-событий нет.</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-dim text-xs uppercase">
                  <tr>
                    <th className="pb-2 pr-3">Time</th>
                    <th className="pb-2 pr-3">Action</th>
                    <th className="pb-2 pr-3">Resource</th>
                    <th className="pb-2 pr-3">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.slice(0, 10).map((a) => (
                    <tr key={a.id} className="border-t border-token">
                      <td className="py-2 text-xs text-dim mono">{formatMskShort(a.ts)}</td>
                      <td className="text-xs mono">{a.action}</td>
                      <td className="text-xs mono text-dim">{a.resource}</td>
                      <td>
                        <span className={`badge badge-${a.outcome === "success" ? "ok" : a.outcome === "denied" ? "warn" : "danger"}`}>
                          {a.outcome}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>
          )}

          {/* 6b. Action result banners */}
          {(actionErr || actionInfo) && (
            <Section
              icon={<Activity className="w-4 h-4" />}
              title="Результат"
              className="col-span-2"
            >
              {actionErr && <div className="alert-danger">{actionErr}</div>}
              {actionInfo && (
                <div className="text-sm text-ok mt-1">{actionInfo}</div>
              )}
            </Section>
          )}

          {/* 6c. API-backed permissions / groups (debug-ish surface) */}
          {!mockMode && (
            <Section
              icon={<ListTree className="w-4 h-4" />}
              title="Backend view (auth_service)"
              className="col-span-2"
            >
              {permsQ.loading || groupsApiQ.loading ? (
                <div className="spinner" aria-label="Loading" />
              ) : permsQ.error ? (
                <div className="alert-danger">{permsQ.error.message}</div>
              ) : permsQ.data ? (
                <div className="grid grid-cols-2 gap-4 text-xs">
                  <div>
                    <div className="text-dim mb-1">Allowed services</div>
                    <div className="flex flex-wrap gap-1">
                      {permsQ.data.allowed_services.map((s) => (
                        <span key={s} className="badge">{s}</span>
                      ))}
                      {permsQ.data.allowed_services.length === 0 && (
                        <span className="text-dim italic">пусто</span>
                      )}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim mb-1">Groups (API)</div>
                    <div className="flex flex-wrap gap-1">
                      {(groupsApiQ.data ?? []).map((g) => (
                        <span key={g.id} className="badge">{g.name}</span>
                      ))}
                      {(!groupsApiQ.data || groupsApiQ.data.length === 0) && (
                        <span className="text-dim italic">не состоит</span>
                      )}
                    </div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-dim mb-1">Effective service roles</div>
                    <table className="w-full text-xs">
                      <tbody>
                        {Object.entries(permsQ.data.service_roles).map(
                          ([svc, roles]) => (
                            <tr key={svc} className="border-t border-token">
                              <td className="py-1 mono">{svc}</td>
                              <td className="text-dim">{roles.join(", ")}</td>
                            </tr>
                          ),
                        )}
                        {Object.keys(permsQ.data.service_roles).length === 0 && (
                          <tr>
                            <td className="py-1 text-dim italic">пусто</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <div className="text-dim italic text-sm">нет данных</div>
              )}
            </Section>
          )}

          {/* 7. Danger zone (smaller, footer) */}
          <Section icon={<Trash2 className="w-4 h-4" />} title="Danger zone" className="col-span-2">
            <div className="flex gap-2 flex-wrap">
              <button
                className="btn btn-danger flex items-center gap-1"
                disabled={!caps.disable || busy !== null}
                title={caps.disable ? undefined : caps.reason}
                onClick={() => {
                  const reason = window.prompt("Причина бана:");
                  if (!reason) return;
                  runAction("ban", () =>
                    banUser(user.id, { ban_type: "permanent", reason }),
                  );
                }}
              >
                <ShieldOff className="w-4 h-4" /> Ban (permanent)
              </button>
              <button
                className="btn"
                disabled={!caps.disable || busy !== null}
                title={caps.disable ? undefined : caps.reason}
                onClick={() => runAction("unban", () => unbanUser(user.id))}
              >
                <ShieldCheck className="w-4 h-4 inline" /> Unban
              </button>
              <button
                className="btn btn-danger-solid"
                disabled={!caps.delete || busy !== null}
                title={caps.delete ? undefined : caps.reason}
                onClick={() => {
                  const reason = window.prompt(
                    "Hard-delete: укажи причину (Q3 reorg / left / ...):",
                  );
                  if (!reason) return;
                  if (!window.confirm(`Снести ${user.username} целиком?`)) return;
                  // После удаления карточки уже нет — уходим к списку, иначе
                  // деталь висит на 404 со stale-данными снесённого юзера.
                  if (mockMode) {
                    setActionInfo("mock: delete");
                    return;
                  }
                  setBusy("delete");
                  setActionErr(null);
                  setActionInfo(null);
                  deleteUser(user.id, { reason })
                    .then(() => navigate("/users"))
                    .catch((e) => setActionErr(apiErrMsg(e)))
                    .finally(() => setBusy(null));
                }}
              >
                Delete user
              </button>
              <span className="text-xs text-dim ml-auto">
                {caps.delete
                  ? "Действия требуют подтверждения и логируются в аудит."
                  : `Нет прав: ${caps.reason}`}
              </span>
            </div>
          </Section>
        </div>
        )}
      </section>
      {showAddGroup && !mockMode && (
        <AddUserToGroupModal
          userId={user.id}
          currentGroupIds={new Set((groupsApiQ.data ?? []).map((g) => g.id))}
          onClose={() => setShowAddGroup(false)}
          onAdded={() => {
            groupsApiQ.refetch();
            setShowAddGroup(false);
            setActionInfo("Пользователь добавлен в группу");
            setActionErr(null);
          }}
          onError={(msg) => {
            setActionErr(msg);
            setActionInfo(null);
          }}
        />
      )}
    </Shell>
  );
}

function PermissionRow({
  entry,
  userId,
  open,
  onToggle,
}: {
  entry: EffectiveEntry;
  userId: string;
  open: boolean;
  onToggle: () => void;
}) {
  const trace = open ? traceForUser(userId, entry.permission) : null;
  return (
    <>
      <tr className="border-t border-token hover-bg cursor-pointer" onClick={onToggle}>
        <td className="px-3 py-2 align-top">
          {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        </td>
        <td className="px-3 py-2 mono text-xs">{entry.permission}</td>
        <td className="px-3 py-2 text-xs">{entry.service}</td>
        <td className="px-3 py-2 text-xs">
          {entry.scope_kind === "platform" && <span className="badge">платформа</span>}
          {entry.scope_kind === "dept" && <span className="badge">отдел · {entry.scope_ref}</span>}
          {entry.scope_kind === "resource" && <span className="badge">{entry.scope_ref}</span>}
        </td>
        <td className="px-3 py-2">
          <div className="flex flex-wrap gap-1">
            {entry.sources.map((s) => (
              <span key={`${s.kind}-${s.id}`} className={`badge badge-${s.kind === "direct" ? "warn" : s.kind === "role" ? "accent" : ""}`}>
                {s.label}
              </span>
            ))}
          </div>
        </td>
      </tr>
      {open && trace && (
        <tr className="border-t border-token surface-2">
          <td></td>
          <td colSpan={4} className="px-3 py-2">
            <div className="text-xs text-dim mb-1">Traceability:</div>
            {trace.roots.length === 0 ? (
              <div className="text-xs text-dim italic">источники не найдены</div>
            ) : (
              <TraceTree nodes={trace.roots} />
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function TraceTree({ nodes }: { nodes: TraceNode[] }) {
  return (
    <ul className="text-xs ml-3">
      {nodes.map((n) => (
        <li key={`${n.source}-${n.id}`} className="my-1">
          <details open>
            <summary className="cursor-pointer flex items-center gap-2 list-none">
              <span className="text-dim">→</span>
              <span className="mono">{n.source}</span>
              <span>·</span>
              <span>{n.label}</span>
              {n.via && n.via.length > 0 && (
                <span className="text-dim">({n.via.length} role{n.via.length === 1 ? "" : "s"})</span>
              )}
            </summary>
            {n.via && n.via.length > 0 && (
              <div className="ml-4 border-l border-token pl-2 mt-1">
                <TraceTree nodes={n.via} />
              </div>
            )}
          </details>
        </li>
      ))}
    </ul>
  );
}

function DiffView({ diff }: { diff: DiffEntry[] }) {
  const added = diff.filter((d) => d.change === "added");
  const removed = diff.filter((d) => d.change === "removed");
  if (added.length === 0 && removed.length === 0) {
    return <div className="text-sm text-dim italic">Effective не изменится — все permissions остаются.</div>;
  }
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="border border-token rounded p-3" style={{ background: "rgba(244,135,113,0.05)" }}>
        <div className="text-xs uppercase tracking-wider text-danger mb-2 flex items-center gap-1">
          − убрано · {removed.length}
        </div>
        {removed.length === 0 ? (
          <div className="text-xs text-dim italic">ничего</div>
        ) : (
          <ul className="text-xs">
            {removed.map((d) => (
              <li key={`rm-${d.permission}-${d.scope_ref}`} className="py-0.5 mono">
                <span className="text-danger">−</span> {d.permission} <span className="text-dim">({d.scope_kind})</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="border border-token rounded p-3" style={{ background: "rgba(106,176,76,0.05)" }}>
        <div className="text-xs uppercase tracking-wider text-ok mb-2 flex items-center gap-1">
          + добавлено · {added.length}
        </div>
        {added.length === 0 ? (
          <div className="text-xs text-dim italic">ничего</div>
        ) : (
          <ul className="text-xs">
            {added.map((d) => (
              <li key={`ad-${d.permission}-${d.scope_ref}`} className="py-0.5 mono">
                <span className="text-ok">+</span> {d.permission} <span className="text-dim">({d.scope_kind})</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-2 py-1.5 border-b border-dashed border-token text-sm last:border-b-0">
      <span className="text-dim">{k}</span>
      <span>{v}</span>
    </div>
  );
}

function DeptBadge({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span className="badge">dept · {label}</span>;
}

function HeaderDeptName({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <>{label}</>;
}

function ModalDeptLabel({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span>{label}</span>;
}

function ServiceCell({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return (
    <span>
      <span>{label}</span>
      {label !== name && (
        <span className="mono text-[10px] text-dim ml-1">{name}</span>
      )}
    </span>
  );
}

// Render granted_by id as a clickable username when the user exists, or
// a dimmed "<id> (удалён)" fallback when the actor has been deleted.
function GrantedBy({ id }: { id: string }) {
  if (!id || id === "system" || id === "<simulated>") {
    return <span className="mono">{id || "—"}</span>;
  }
  const u = userById(id);
  if (!u) {
    return (
      <span className="mono text-dim" title="user removed or unknown">
        {id} (удалён)
      </span>
    );
  }
  return (
    <Link to={`/users/${u.id}`} className="mono hover-bg">
      {u.username}
    </Link>
  );
}

// Silence unused-import warning (kept for future expansions of in-memory mutations).
void GROUP_ASSIGNMENTS;

// ---------------------------------------------------------------------------
// Sessions tab — admin view of target user's active refresh sessions
// ---------------------------------------------------------------------------

interface UserSessionsTabProps {
  userId: string;
  mockMode: boolean;
  canRevoke: boolean;
  canRevokeReason: string;
  refreshSignal?: number;
}

function UserSessionsTab({
  userId,
  mockMode,
  canRevoke,
  canRevokeReason,
  refreshSignal,
}: UserSessionsTabProps) {
  const sessQ = useQuery<SessionListResponse>(
    () => listUserSessions(userId),
    [userId, refreshSignal ?? 0],
    { enabled: !mockMode && !!userId },
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function revokeAll() {
    if (mockMode) {
      setInfo("mock: revoke all");
      return;
    }
    if (!window.confirm("Завершить ВСЕ сессии этого пользователя?")) return;
    setBusy("all");
    setErr(null);
    setInfo(null);
    try {
      const r = await revokeUserAllSessions(userId);
      setInfo(`Отозвано сессий: ${r.revoked_count}`);
      sessQ.refetch();
    } catch (e) {
      setErr(
        apiErrMsg(e),
      );
    } finally {
      setBusy(null);
    }
  }

  async function revokeOne(sid: string) {
    if (mockMode) {
      setInfo(`mock: revoke ${sid}`);
      return;
    }
    setBusy(sid);
    setErr(null);
    setInfo(null);
    try {
      await revokeUserSessionById(userId, sid);
      setInfo("Сессия завершена");
      sessQ.refetch();
    } catch (e) {
      setErr(
        apiErrMsg(e),
      );
    } finally {
      setBusy(null);
    }
  }

  const sessions = sessQ.data?.items ?? [];

  return (
    <div className="flex-1 overflow-y-auto p-5">
      <div className="surface border border-token rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-xs uppercase tracking-wider text-dim flex items-center gap-2">
            <Monitor className="w-4 h-4" /> Активные сессии · {sessions.length}
          </div>
          <button
            className="btn btn-danger flex items-center gap-1"
            onClick={revokeAll}
            disabled={!canRevoke || busy !== null || sessions.length === 0}
            title={canRevoke ? undefined : canRevokeReason}
          >
            <LogOut className="w-4 h-4" /> Завершить все сессии
          </button>
        </div>

        {!mockMode && sessQ.loading && (
          <div className="text-xs text-dim py-6 text-center">Загрузка…</div>
        )}
        {!mockMode && sessQ.error && (
          <div className="alert-danger">{sessQ.error.message}</div>
        )}
        {!mockMode && !sessQ.loading && !sessQ.error && sessions.length === 0 && (
          <div className="empty-card">Активных сессий нет.</div>
        )}
        {mockMode && (
          <div className="text-xs text-dim italic py-4">
            mock-режим: список сессий не подгружается.
          </div>
        )}

        {sessions.length > 0 && (
          <div className="border border-token rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase surface-2">
                <tr>
                  <th className="px-3 py-2">session_id</th>
                  <th className="px-3 py-2">IP</th>
                  <th className="px-3 py-2">UA</th>
                  <th className="px-3 py-2">created</th>
                  <th className="px-3 py-2">last_used</th>
                  <th className="px-3 py-2">expires</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id} className="border-t border-token">
                    <td className="px-3 py-2 mono text-xs">
                      {s.session_id.slice(0, 12)}…
                    </td>
                    <td className="px-3 py-2 mono text-xs">
                      {s.ip_address ?? "—"}
                    </td>
                    <td
                      className="px-3 py-2 text-xs truncate max-w-[220px]"
                      title={s.user_agent ?? ""}
                    >
                      {s.user_agent ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-xs text-dim mono">
                      {fmtSessTs(s.created_at)}
                    </td>
                    <td className="px-3 py-2 text-xs text-dim mono">
                      {fmtSessTs(s.last_used_at)}
                    </td>
                    <td className="px-3 py-2 text-xs text-dim mono">
                      {fmtSessTs(s.expires_at)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        className="btn btn-sm btn-danger flex items-center gap-1 ml-auto"
                        disabled={!canRevoke || busy !== null}
                        title={canRevoke ? "Завершить сессию" : canRevokeReason}
                        onClick={() => revokeOne(s.session_id)}
                      >
                        <XCircle className="w-3 h-3" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {(err || info) && (
          <div className="mt-3">
            {err && <div className="alert-danger">{err}</div>}
            {info && <div className="text-sm text-ok mt-1">{info}</div>}
          </div>
        )}
        {!canRevoke && (
          <div className="text-xs text-dim italic mt-3">
            Read-only: {canRevokeReason}
          </div>
        )}
      </div>
    </div>
  );
}

const fmtSessTs = formatMskShort;

// ---------------------------------------------------------------------------
// Live (API-backed) groups section: per-row remove + open add-group modal.
// Used in non-mock mode; mock mode keeps the rich permission-graph rendering.
// ---------------------------------------------------------------------------

interface UserGroupsLiveSectionProps {
  userId: string;
  apiGroups: ApiGroup[] | null;
  loading: boolean;
  error: ApiError | Error | null;
  canManage: boolean;
  capsReason: string;
  onChanged: () => void;
  onOpenAdd: () => void;
  onActionError: (msg: string) => void;
  onActionInfo: (msg: string) => void;
}

function UserGroupsLiveSection({
  userId,
  apiGroups,
  loading,
  error,
  canManage,
  capsReason,
  onChanged,
  onOpenAdd,
  onActionError,
  onActionInfo,
}: UserGroupsLiveSectionProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const list = apiGroups ?? [];

  async function handleRemove(groupId: string, groupName: string) {
    if (
      !window.confirm(`Убрать пользователя из группы «${groupName}»?`)
    ) {
      return;
    }
    setBusy(groupId);
    try {
      await removeUserFromGroup(userId, groupId);
      onActionInfo(`Пользователь убран из группы «${groupName}»`);
      onChanged();
    } catch (e) {
      onActionError(
        apiErrMsg(e),
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <Section
      icon={<UsersRound className="w-4 h-4" />}
      title={
        <span className="flex items-center gap-2 w-full">
          <span>Членство в группах · {list.length}</span>
          <button
            className="btn btn-sm flex items-center gap-1 ml-auto"
            disabled={!canManage}
            title={canManage ? "Добавить в группу" : capsReason}
            onClick={onOpenAdd}
          >
            <Plus className="w-3 h-3" /> Добавить
          </button>
        </span>
      }
    >
      {loading ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : error ? (
        <div className="alert-danger">{error.message}</div>
      ) : list.length === 0 ? (
        <div className="text-sm text-dim italic">
          Не состоит ни в одной группе.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {list.map((g) => (
            <div
              key={g.id}
              className="row-line rounded px-2 -mx-2 hover-bg"
              style={{ display: "flex", alignItems: "center", gap: 8 }}
            >
              <Link
                to={`/users/group/${g.id}`}
                className="font-medium text-sm hover-bg"
              >
                {g.name}
              </Link>
              <DeptBadge deptId={g.department_id} />
              <span
                className="text-xs text-dim truncate max-w-[220px]"
                title={g.description ?? ""}
              >
                {g.description ?? ""}
              </span>
              <button
                className="btn btn-sm btn-danger ml-auto flex items-center gap-1"
                disabled={!canManage || busy === g.id}
                title={canManage ? "Убрать из группы" : capsReason}
                onClick={() => handleRemove(g.id, g.name)}
              >
                <X className="w-3 h-3" /> Убрать
              </button>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Add-to-group modal: lists all groups minus already joined; submit calls
// addUserToGroup. Selection is single-group per submit.
// ---------------------------------------------------------------------------

interface AddUserToGroupModalProps {
  userId: string;
  currentGroupIds: Set<string>;
  onClose: () => void;
  onAdded: () => void;
  onError: (msg: string) => void;
}

function AddUserToGroupModal({
  userId,
  currentGroupIds,
  onClose,
  onAdded,
  onError,
}: AddUserToGroupModalProps) {
  const groupsQ = useQuery<{ items: ApiGroup[]; total: number }>(
    () => listGroupsWithTotal({ limit: 200 }),
    [],
  );
  const { depts: deptMap } = useLabelMaps();
  const [selected, setSelected] = useState<string>("");
  const [filter, setFilter] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  const candidates = useMemo(() => {
    const all = groupsQ.data?.items ?? [];
    const f = filter.trim().toLowerCase();
    return all
      .filter((g) => !currentGroupIds.has(g.id))
      .filter((g) => {
        if (!f) return true;
        const deptLabel = (deptMap.get(g.department_id) ?? g.department_id).toLowerCase();
        return (
          g.name.toLowerCase().includes(f) ||
          deptLabel.includes(f)
        );
      });
  }, [groupsQ.data, currentGroupIds, filter, deptMap]);

  async function submit() {
    if (!selected) return;
    setSubmitting(true);
    try {
      await addUserToGroup(userId, selected);
      onAdded();
    } catch (e) {
      onError(
        apiErrMsg(e),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card surface border border-token rounded-lg p-5 w-[480px] max-w-[95vw]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center mb-3">
          <h3 className="font-semibold text-base">Добавить в группу</h3>
          <button className="btn btn-sm btn-ghost ml-auto" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <input
          className="surface-2 border border-token rounded px-2 py-1 w-full text-sm mb-3"
          placeholder="Фильтр по имени / отделу…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {groupsQ.loading ? (
          <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
        ) : groupsQ.error ? (
          <div className="alert-danger">{groupsQ.error.message}</div>
        ) : candidates.length === 0 ? (
          <div className="text-sm text-dim italic py-2">
            Подходящих групп нет — пользователь уже состоит во всех доступных.
          </div>
        ) : (
          <div className="border border-token rounded max-h-[320px] overflow-y-auto">
            {candidates.map((g) => (
              <label
                key={g.id}
                className="flex items-start gap-2 px-3 py-2 border-b border-token last:border-b-0 cursor-pointer hover-bg"
              >
                <input
                  type="radio"
                  name="group-pick"
                  value={g.id}
                  checked={selected === g.id}
                  onChange={() => setSelected(g.id)}
                  className="mt-1"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium truncate">
                    {g.name}
                  </div>
                  <div className="text-xs text-dim flex gap-2 mt-0.5">
                    <ModalDeptLabel deptId={g.department_id} />
                    {g.description && (
                      <span className="truncate">· {g.description}</span>
                    )}
                  </div>
                </div>
              </label>
            ))}
          </div>
        )}
        <TruncationNotice
          className="mt-2"
          shown={groupsQ.data?.items.length ?? 0}
          total={groupsQ.data?.total ?? null}
        />
        <div className="flex items-center gap-2 mt-4">
          <button className="btn ml-auto" onClick={onClose}>
            Отмена
          </button>
          <button
            className="btn btn-accent"
            disabled={!selected || submitting}
            onClick={submit}
          >
            {submitting ? "Добавляю…" : "Добавить"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live service-roles table (read-only view) and per-service editor.
// Live roles come from getUserPermissions().service_roles
// (Record<service, string[]>).
// ---------------------------------------------------------------------------

function UserServiceRolesLiveTable({
  roles,
  loading,
  error,
}: {
  roles: Record<string, string[]>;
  loading: boolean;
  error: ApiError | Error | null;
}) {
  if (loading) {
    return <div className="text-xs text-dim">Загрузка…</div>;
  }
  if (error) {
    return <div className="alert-danger">{error.message}</div>;
  }
  const entries = Object.entries(roles).filter(
    ([, list]) => Array.isArray(list) && list.length > 0,
  );
  if (entries.length === 0) {
    return (
      <div className="text-sm text-dim italic">Сервис-роли не выданы.</div>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-dim text-xs uppercase">
        <tr>
          <th className="pb-2 pr-3">Сервис</th>
          <th className="pb-2">Роли</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([svc, list]) => (
          <tr key={svc} className="border-t border-token">
            <td className="py-2">
              <ServiceCell name={svc} />
            </td>
            <td>
              <div className="flex flex-wrap gap-1">
                {list.map((r) => (
                  <span key={r} className="badge badge-accent">
                    {r}
                  </span>
                ))}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface UserServiceRolesEditorProps {
  userId: string;
  deptId: string | null;
  currentRoles: Record<string, string[]>;
  onClose: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
}

function UserServiceRolesEditor({
  userId,
  deptId,
  currentRoles,
  onClose,
  onSaved,
  onError,
}: UserServiceRolesEditorProps) {
  const servicesQ = useQuery<ApiService[]>(() => listServices(), []);
  const [submitting, setSubmitting] = useState(false);

  // Per-service local selection. Init from currentRoles.
  const [selection, setSelection] = useState<Record<string, Set<string>>>(
    () => {
      const m: Record<string, Set<string>> = {};
      for (const [svc, list] of Object.entries(currentRoles)) {
        m[svc] = new Set(list);
      }
      return m;
    },
  );

  function toggle(svc: string, role: string) {
    setSelection((prev) => {
      const next = { ...prev };
      const set = new Set(next[svc] ?? []);
      if (set.has(role)) set.delete(role);
      else set.add(role);
      next[svc] = set;
      return next;
    });
  }

  async function save() {
    if (!deptId) {
      onError(
        "У пользователя не задан department_id — нельзя редактировать сервис-роли.",
      );
      return;
    }
    setSubmitting(true);
    try {
      const services = servicesQ.data ?? [];
      // Push replace-set for every service that has either a current
      // assignment or a new one — so an emptied selection downgrades to "no
      // roles in this service".
      const touched = new Set<string>([
        ...Object.keys(currentRoles),
        ...Object.keys(selection),
        ...services.map((s) => s.service_name),
      ]);
      // Filter to services known to the platform; users.py 422's on unknown
      // service names.
      const known = new Set(services.map((s) => s.service_name));
      // Каждый сервис — отдельный replace-вызов. Один упавший не должен
      // прятать те, что применились, и не должен срывать остальные.
      const targets: string[] = [];
      for (const svc of touched) {
        if (!known.has(svc)) continue;
        const before = new Set(currentRoles[svc] ?? []);
        const after = selection[svc] ?? new Set<string>();
        // Skip no-op (avoid noisy audit + unnecessary calls).
        if (
          before.size === after.size &&
          [...before].every((r) => after.has(r))
        ) {
          continue;
        }
        targets.push(svc);
      }
      if (targets.length === 0) {
        onSaved();
        onClose();
        return;
      }
      const results = await Promise.allSettled(
        targets.map((svc) =>
          assignUserRoles(userId, {
            service_name: svc,
            roles: [...(selection[svc] ?? new Set<string>())],
          }),
        ),
      );
      const failures = results
        .map((res, i) =>
          res.status === "rejected"
            ? `${targets[i]} — ${apiErrMsg(res.reason)}`
            : null,
        )
        .filter((x): x is string => x !== null);
      if (failures.length > 0) {
        const applied = targets.length - failures.length;
        // Часть изменений уже на сервере — рефетчим свежие данные, затем
        // показываем итог (onError ставится последним, чтобы пережить
        // очистку ошибки внутри onSaved).
        onSaved();
        onError(
          `Применено сервисов: ${applied} из ${targets.length}. ` +
            `Не удалось ${failures.length}: ${failures.join("; ")}`,
        );
      } else {
        onSaved();
        onClose();
      }
    } catch (e) {
      onError(
        apiErrMsg(e),
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!deptId) {
    return (
      <div className="alert-danger">
        У пользователя не задан department_id — сервис-роли назначаются
        только в рамках отдела.
      </div>
    );
  }

  return (
    <div className="border border-token rounded p-3">
      <div className="text-xs text-dim mb-3">
        Replace-семантика: отмеченные роли заменят текущий набор юзера в
        соответствующем сервисе. Снять все — оставить ничего не выбранным.
      </div>
      {servicesQ.loading ? (
        <div className="text-xs text-dim">Загрузка списка сервисов…</div>
      ) : servicesQ.error ? (
        <div className="alert-danger">{servicesQ.error.message}</div>
      ) : (
        <div className="flex flex-col gap-3">
          {(servicesQ.data ?? []).map((svc) => (
            <ServiceRolesPickerRow
              key={svc.service_name}
              deptId={deptId}
              serviceName={svc.service_name}
              displayName={svc.service_name}
              selected={selection[svc.service_name] ?? new Set<string>()}
              onToggle={(role) => toggle(svc.service_name, role)}
            />
          ))}
        </div>
      )}
      <div className="flex items-center gap-2 mt-4">
        <button className="btn ml-auto" onClick={onClose} disabled={submitting}>
          Отмена
        </button>
        <button
          className="btn btn-accent"
          disabled={submitting || servicesQ.loading}
          onClick={save}
        >
          {submitting ? "Сохраняю…" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

function ServiceRolesPickerRow({
  deptId,
  serviceName,
  displayName,
  selected,
  onToggle,
}: {
  deptId: string;
  serviceName: ServiceName;
  displayName: string;
  selected: Set<string>;
  onToggle: (role: string) => void;
}) {
  const rolesQ = useQuery<ApiServiceRole[]>(
    () => listServiceRoles(deptId, serviceName),
    [deptId, serviceName],
  );
  return (
    <div className="surface-2 border border-token rounded p-3">
      <div className="text-sm font-medium mb-2 flex items-center gap-2">
        <span className="mono">{serviceName}</span>
        <span className="text-dim font-normal text-xs">{displayName}</span>
      </div>
      {rolesQ.loading ? (
        <div className="text-xs text-dim">Загрузка ролей…</div>
      ) : rolesQ.error ? (
        // 404 / 403 — этот сервис недоступен для отдела. Показываем тихо.
        <div className="text-xs text-dim italic">
          Роли недоступны: {rolesQ.error.message}
        </div>
      ) : (rolesQ.data ?? []).length === 0 ? (
        <div className="text-xs text-dim italic">
          В отделе нет ролей для этого сервиса.
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {(rolesQ.data ?? []).map((r) => {
            const on = selected.has(r.role_name);
            return (
              <label
                key={r.role_name}
                className={`badge ${on ? "badge-accent" : ""} cursor-pointer`}
                title={r.description ?? ""}
              >
                <input
                  type="checkbox"
                  className="mr-1"
                  checked={on}
                  onChange={() => onToggle(r.role_name)}
                />
                {r.role_name}
                {r.is_system && (
                  <span className="ml-1 text-dim">·sys</span>
                )}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
