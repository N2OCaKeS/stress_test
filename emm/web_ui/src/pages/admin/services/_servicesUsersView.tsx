/**
 * Rich user-detail view used by /admin/services.users middle workzone.
 *
 * Sections: Profile · Permissions · Groups · Sessions · Danger zone.
 * Each section pulls live data via auth_service API and exposes mutations
 * gated by `userMutationCaps(persona, target.dept_id)`.
 *
 * Standalone /users/<id> page (UserDetail.tsx) remains the full-page version
 * with diff/trace mocks and the access matrix; this component is the
 * inline-workzone counterpart and shares only the data-fetching shape.
 */

import { useMemo, useState } from "react";
import {
  User as UserIcon,
  Edit3,
  KeyRound,
  Pause,
  Play,
  Unlock,
  LogOut,
  Trash2,
  AlertTriangle,
  ShieldOff,
  ShieldCheck,
  UsersRound,
  Cog,
  Monitor,
  XCircle,
  Plus,
  ListTree,
} from "lucide-react";
import { Tabs } from "@/components/ui/Tabs";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { userMutationCaps } from "@/lib/rbac";
import { formatMsk, formatMskShort } from "@/lib/datetime";
import {
  banUser,
  deleteUser,
  disableUser,
  enableUser,
  getUser,
  getUserGroups,
  getUserPermissions,
  listUserSessions,
  normalizeUserStatus,
  resetUserPassword,
  userStatusBadgeKind,
  revokeUserAllSessions,
  revokeUserSessionById,
  unbanUser,
  unlockUser,
  assignUserRoles,
} from "@/api/auth/users";
import {
  addUserToGroup,
  removeUserFromGroup,
  listGroupsWithTotal,
} from "@/api/auth/groups";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { listServiceRoles } from "@/api/auth/service_roles";
import { listServices } from "@/api/auth/services";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import type {
  Group,
  Service,
  ServiceName,
  ServiceRole,
  SessionListResponse,
  User as ApiUser,
  UserPermissionsResponse,
} from "@/api/auth/types";
import { StatRow } from "./_inline";
import { useDeptLabel, useLabelMaps, useServiceLabel } from "@/lib/labels";

interface Props {
  userId: string;
  /** Hide platform_role / cross-dept bits when caller has external state. */
  fallbackUsername?: string;
  fallbackDeptId?: string | null;
  /** Bumped by parent on list refresh so we re-fetch on outside changes. */
  refetchListSignal?: number;
  /** Called after successful mutation so the parent list updates too. */
  onChanged?: () => void;
  /** Switch to edit mode (parent owns the form). */
  onStartEdit?: () => void;
}

type TabId = "profile" | "groups" | "sessions" | "roles" | "danger";

export function UserBackendView({
  userId,
  refetchListSignal,
  onChanged,
  onStartEdit,
}: Props) {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const confirm = useConfirm();

  const userQ = useQuery<ApiUser>(
    () => getUser(userId),
    [userId, refetchListSignal ?? 0],
    { enabled: !mockMode && !!userId },
  );
  const permsQ = useQuery<UserPermissionsResponse>(
    () => getUserPermissions(userId),
    [userId, refetchListSignal ?? 0],
    { enabled: !mockMode && !!userId },
  );
  const groupsQ = useQuery<Group[]>(
    () => getUserGroups(userId),
    [userId, refetchListSignal ?? 0],
    { enabled: !mockMode && !!userId },
  );

  const user = userQ.data;
  const caps = useMemo(
    () => userMutationCaps(persona, user?.department_id ?? null),
    [persona, user?.department_id],
  );

  const [tab, setTab] = useState<TabId>("profile");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<unknown>) {
    if (mockMode) {
      setInfo(`mock: ${label} (no-op)`);
      setErr(null);
      return;
    }
    setBusy(label);
    setErr(null);
    setInfo(null);
    try {
      await fn();
      setInfo(`${label}: OK`);
      userQ.refetch();
      permsQ.refetch();
      groupsQ.refetch();
      onChanged?.();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setBusy(null);
    }
  }

  if (!mockMode && userQ.loading && !user) {
    return <div className="spinner mx-auto my-8" aria-label="Загрузка" />;
  }
  if (!mockMode && userQ.error) {
    return <div className="alert-danger">{userQ.error.message}</div>;
  }
  if (!user && !mockMode) {
    return <div className="text-sm text-dim italic">Пользователь не найден.</div>;
  }

  // Mock fallback: synthesise minimum shape from URL id so the panel renders
  // something even without backend connectivity.
  const u: ApiUser =
    user ??
    ({
      id: userId,
      username: userId,
      email: null,
      department_id: null,
      department_name: null,
      platform_role: null as unknown as ApiUser["platform_role"],
      status: "active",
      is_active: true,
      is_banned: false,
      created_at: "",
    } as ApiUser);

  return (
    <div className="flex flex-col gap-4">
      {/* Sticky header with primary admin actions */}
      <div className="card">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-10 h-10 rounded-full bg-accent flex items-center justify-center text-sm font-semibold shrink-0">
              {u.username.slice(0, 2).toUpperCase()}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-semibold truncate">{u.username}</h3>
                <span className={`badge badge-${userStatusBadgeKind(u.status)}`}>
                  {normalizeUserStatus(u.status)}
                </span>
                {u.is_banned && <span className="badge badge-danger">banned</span>}
                {u.platform_role && (
                  <span className="badge badge-accent">{u.platform_role}</span>
                )}
                {u.must_change_password && (
                  <span className="badge badge-warn" title="must_change_password">
                    pwd!
                  </span>
                )}
              </div>
              <div className="text-xs text-dim truncate">
                {u.email ?? "—"} · <span className="mono">{u.id}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap shrink-0">
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.edit || busy !== null}
              title={caps.edit ? undefined : caps.reason}
              onClick={() => onStartEdit?.()}
            >
              <Edit3 className="w-4 h-4" /> Изменить
            </button>
            <button
              className="btn flex items-center gap-1"
              disabled={!caps.edit || busy !== null}
              title={caps.edit ? undefined : caps.reason}
              onClick={async () => {
                const { ok, reason: pwd } = await confirm.prompt({
                  title: "Сброс пароля",
                  message: "Новый пароль (min 12, буквы + цифры):",
                  reason: true,
                  reasonSecret: true,
                  reasonRequired: true,
                  confirmLabel: "Сбросить",
                });
                if (!ok || !pwd) return;
                run("reset-password", () =>
                  resetUserPassword(u.id, { new_password: pwd }),
                );
              }}
            >
              <KeyRound className="w-4 h-4" /> Сброс пароля
            </button>
            {normalizeUserStatus(u.status) === "active" ? (
              <button
                className="btn btn-danger flex items-center gap-1"
                disabled={!caps.disable || busy !== null}
                title={caps.disable ? undefined : caps.reason}
                onClick={() => run("disable", () => disableUser(u.id))}
              >
                <Pause className="w-4 h-4" /> Заблокировать
              </button>
            ) : (
              <button
                className="btn flex items-center gap-1"
                disabled={!caps.disable || busy !== null}
                title={caps.disable ? undefined : caps.reason}
                onClick={() => run("enable", () => enableUser(u.id))}
              >
                <Play className="w-4 h-4" /> Разблокировать
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
              onClick={() => run("unlock", () => unlockUser(u.id))}
            >
              <Unlock className="w-4 h-4" /> Сброс lockout
            </button>
            {!caps.edit && !caps.disable && !caps.delete && (
              <span className="badge badge-warn" title={caps.reason}>
                Только чтение
              </span>
            )}
          </div>
        </div>
        {err && <div className="alert-danger mt-3">{err}</div>}
        {info && <div className="text-xs text-ok mt-3">{info}</div>}
      </div>

      <Tabs
        active={tab}
        onChange={(id) => setTab(id as TabId)}
        className="border-b border-token flex gap-1 shrink-0"
        tabs={[
          {
            id: "profile",
            label: "Профиль",
            icon: <UserIcon className="w-3 h-3" />,
          },
          {
            id: "roles",
            label: "Service-роли",
            icon: <Cog className="w-3 h-3" />,
            count: permsQ.data
              ? Object.values(permsQ.data.service_roles ?? {}).reduce(
                  (n, arr) => n + arr.length,
                  0,
                )
              : undefined,
          },
          {
            id: "groups",
            label: "Группы",
            icon: <UsersRound className="w-3 h-3" />,
            count: groupsQ.data?.length,
          },
          {
            id: "sessions",
            label: "Сессии",
            icon: <Monitor className="w-3 h-3" />,
          },
          {
            id: "danger",
            label: "Опасная зона",
            icon: <Trash2 className="w-3 h-3" />,
          },
        ]}
      />

      {tab === "profile" && (
        <ProfileTab user={u} perms={permsQ.data} permsLoading={permsQ.loading} />
      )}
      {tab === "roles" && (
        <RolesTab
          userId={u.id}
          deptId={u.department_id}
          perms={permsQ.data}
          loading={permsQ.loading}
          err={permsQ.error}
          canManage={caps.manageRoles}
          capsReason={caps.reason}
          mockMode={mockMode}
          run={run}
          busy={busy}
        />
      )}
      {tab === "groups" && (
        <GroupsTab
          userId={u.id}
          deptId={u.department_id}
          groups={groupsQ.data ?? []}
          loading={groupsQ.loading}
          err={groupsQ.error}
          canManage={caps.manageRoles}
          capsReason={caps.reason}
          mockMode={mockMode}
          run={run}
          busy={busy}
        />
      )}
      {tab === "sessions" && (
        <SessionsTab
          userId={u.id}
          canRevoke={caps.disable}
          capsReason={caps.reason}
          mockMode={mockMode}
        />
      )}
      {tab === "danger" && (
        <DangerTab
          user={u}
          caps={caps}
          run={run}
          busy={busy}
          mockMode={mockMode}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function ProfileTab({
  user,
  perms,
  permsLoading,
}: {
  user: ApiUser;
  perms?: UserPermissionsResponse;
  permsLoading: boolean;
}) {
  return (
    <div className="card">
      <div className="grid grid-cols-2 gap-x-6">
        <div>
          <StatRow k="login" v={<span className="mono">{user.username}</span>} />
          <StatRow k="email" v={<span className="mono">{user.email ?? "—"}</span>} />
          <StatRow k="ID" v={<span className="mono">{user.id}</span>} />
          <StatRow
            k="dept"
            v={<UserDeptLabel name={user.department_name} id={user.department_id} />}
          />
          <StatRow k="platform_role" v={user.platform_role ?? "—"} />
          <div className="text-[11px] text-dim mt-1">
            Платформенная роль определяет доступ к{" "}
            <span className="mono">loging_service</span>: чтение аудита —{" "}
            <span className="mono">loging_reader</span>, управление
            rules/retention — <span className="mono">loging_admin</span>.
            Эти роли работают cross-dept и не требуют отдела.
          </div>
        </div>
        <div>
          <StatRow
            k="status"
            v={
              <span className={`badge badge-${userStatusBadgeKind(user.status)}`}>
                {normalizeUserStatus(user.status)}
              </span>
            }
          />
          <StatRow
            k="is_active"
            v={user.is_active ? "true" : <span className="text-warn">false</span>}
          />
          <StatRow
            k="is_banned"
            v={user.is_banned ? <span className="text-danger">true</span> : "false"}
          />
          <StatRow
            k="must_change_password"
            v={
              user.must_change_password ? (
                <span className="text-warn">да</span>
              ) : (
                "нет"
              )
            }
          />
          <StatRow
            k="created_at"
            v={<span className="mono text-xs">{formatMsk(user.created_at)}</span>}
          />
          <StatRow
            k="updated_at"
            v={<span className="mono text-xs">{formatMsk(user.updated_at)}</span>}
          />
        </div>
      </div>

      {/* Allowed services teaser pulled from /permissions, if available. */}
      <div className="mt-4 pt-3 border-t border-token">
        <div className="text-xs uppercase tracking-wider text-dim mb-2 flex items-center gap-2">
          <ListTree className="w-3 h-3" /> Доступные сервисы
        </div>
        {permsLoading && !perms ? (
          <div className="spinner" aria-label="Загрузка" />
        ) : !perms || perms.allowed_services.length === 0 ? (
          <div className="text-xs text-dim italic">
            ни одного сервиса — пользователь не входит ни в одну группу.
          </div>
        ) : (
          <div className="flex flex-wrap gap-1">
            {perms.allowed_services.map((s) => (
              <span key={s} className="badge">
                {s}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RolesTab({
  userId,
  deptId,
  perms,
  loading,
  err,
  canManage,
  capsReason,
  mockMode,
  run,
  busy,
}: {
  userId: string;
  deptId: string | null;
  perms?: UserPermissionsResponse;
  loading: boolean;
  err: Error | null;
  canManage: boolean;
  capsReason: string;
  mockMode: boolean;
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>;
  busy: string | null;
}) {
  // Редактирование уже назначенного сервиса: сервис известен и залочен.
  const [editService, setEditService] = useState<ServiceName | null>(null);
  // Назначение в новом сервисе: сервис выбирается в самой модалке.
  const [assigning, setAssigning] = useState(false);

  if (loading && !perms) return <div className="spinner" aria-label="Загрузка" />;
  if (err) return <div className="alert-danger">{err.message}</div>;

  const directBySvc = new Map<ServiceName, string[]>();
  for (const r of perms?.direct_service_roles ?? []) {
    const arr = directBySvc.get(r.service_name) ?? [];
    arr.push(r.role_name);
    directBySvc.set(r.service_name, arr);
  }

  const effective = perms?.service_roles ?? ({} as Record<ServiceName, string[]>);

  // Union of services in either bucket.
  const services = Array.from(
    new Set([...Object.keys(effective), ...directBySvc.keys()]),
  ) as ServiceName[];

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold flex items-center gap-2">
          <Cog className="w-4 h-4 text-accent" /> Действующие сервис-роли
        </div>
        <div className="text-xs text-dim">
          источник: <span className="mono">GET /users/{userId}/permissions</span>
        </div>
      </div>
      {services.length === 0 ? (
        <div className="text-sm text-dim italic">
          Сервис-роли отсутствуют (нет ни прямых assignments, ни через группы).
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">Сервис</th>
              <th className="pb-2 pr-3">Действующие роли</th>
              <th className="pb-2 pr-3">Напрямую</th>
              <th className="pb-2 pr-3">Из групп</th>
              <th className="pb-2 text-right"></th>
            </tr>
          </thead>
          <tbody>
            {services.map((svc) => {
              const eff = effective[svc] ?? [];
              const direct = directBySvc.get(svc) ?? [];
              const groupOnly = eff.filter((r) => !direct.includes(r));
              return (
                <tr key={svc} className="border-t border-token align-top">
                  <td className="py-2 pr-3 text-xs">
                    <ServiceInline name={String(svc)} />
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-wrap gap-1">
                      {eff.length === 0 ? (
                        <span className="text-dim italic text-xs">—</span>
                      ) : (
                        eff.map((r) => (
                          <span key={r} className="badge badge-accent">
                            {r}
                          </span>
                        ))
                      )}
                    </div>
                  </td>
                  <td className="py-2 pr-3">
                    {direct.length === 0 ? (
                      <span className="text-dim italic text-xs">—</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {direct.map((r) => (
                          <span key={r} className="badge">
                            {r}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs text-dim">
                    {groupOnly.length === 0 ? "—" : groupOnly.join(", ")}
                  </td>
                  <td className="py-2 text-right">
                    <button
                      className="btn btn-sm"
                      disabled={!canManage}
                      title={canManage ? "Изменить набор ролей" : capsReason}
                      onClick={() => setEditService(svc)}
                    >
                      <Edit3 className="w-3 h-3 inline" /> Изменить
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div className="border-t border-token pt-3">
        <button
          className="btn btn-sm flex items-center gap-1"
          disabled={!canManage}
          title={canManage ? "Добавить роль в другом сервисе" : capsReason}
          onClick={() => setAssigning(true)}
        >
          <Plus className="w-3 h-3" /> Назначить в другом сервисе
        </button>
        {!canManage && (
          <span className="text-xs text-dim italic ml-2">{capsReason}</span>
        )}
      </div>

      {(assigning || editService) && (
        <AssignRolesModal
          userId={userId}
          deptId={deptId}
          initialService={editService}
          lockService={!!editService}
          initialRoles={
            editService ? directBySvc.get(editService) ?? [] : []
          }
          onClose={() => {
            setAssigning(false);
            setEditService(null);
          }}
          onSaved={() => {
            setAssigning(false);
            setEditService(null);
          }}
          mockMode={mockMode}
          run={run}
          busy={busy}
        />
      )}
    </div>
  );
}

// Единая модалка назначения: первый селект выбирает сервис, второй —
// мульти-список его ролей. При редактировании уже назначенного сервиса он
// приходит в `initialService` и залочен (`lockService`), `initialRoles`
// предзаполняют чекбоксы.
function AssignRolesModal({
  userId,
  deptId,
  initialService,
  lockService,
  initialRoles,
  onClose,
  onSaved,
  mockMode,
  run,
  busy,
}: {
  userId: string;
  deptId: string | null;
  initialService: ServiceName | null;
  lockService: boolean;
  initialRoles: string[];
  onClose: () => void;
  onSaved: () => void;
  mockMode: boolean;
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>;
  busy: string | null;
}) {
  const [service, setService] = useState<ServiceName | "">(
    initialService ?? "",
  );
  const [selected, setSelected] = useState<string[]>(initialRoles);
  const [manual, setManual] = useState("");

  // Каталог сервисов для селекта-1 (фильтр по NON_ROLE_SERVICES). Не нужен,
  // если сервис залочен на редактировании.
  const servicesQ = useQuery<Service[]>(() => listServices(), [], {
    enabled: !mockMode && !lockService,
  });
  const serviceOptions = mockMode
    ? MOCK_PICKER_SERVICES
    : (servicesQ.data ?? [])
        .filter((s) => !NON_ROLE_SERVICES.has(s.service_name))
        .map((s) => s.service_name);

  // Каталог ролей выбранного сервиса для селекта-2. Живёт в
  // `/departments/{dept}/services/{svc}/roles` — без dept подгрузить нельзя,
  // тогда работает только ручной ввод.
  const rolesQ = useQuery<ServiceRole[]>(
    () => listServiceRoles(deptId ?? "", service),
    [deptId, service],
    { enabled: !mockMode && !!deptId && !!service },
  );
  const available = service ? rolesQ.data ?? [] : [];

  function pickService(next: string) {
    setService(next as ServiceName);
    // Роли одного сервиса не должны утекать в другой.
    setSelected([]);
    setManual("");
  }

  function toggle(name: string) {
    setSelected((cur) =>
      cur.includes(name) ? cur.filter((r) => r !== name) : [...cur, name],
    );
  }

  async function save() {
    if (!service) return;
    const svc = service;
    await run(`assign-roles:${svc}`, () =>
      assignUserRoles(userId, { service_name: svc, roles: selected }),
    );
    onSaved();
  }

  const title = lockService
    ? `Роли · ${initialService}`
    : "Назначить роль в другом сервисе";

  return (
    <ModalShell title={title} onClose={onClose}>
      <div className="text-xs text-dim mb-1">Сервис:</div>
      {lockService ? (
        <input className="input w-full mono" value={String(service)} disabled />
      ) : (
        <>
          {!mockMode && servicesQ.loading && (
            <div className="spinner" aria-label="Загрузка" />
          )}
          {!mockMode && servicesQ.error && (
            <div className="alert-danger text-xs">
              {servicesQ.error.message}
            </div>
          )}
          <select
            className="input w-full"
            value={service}
            onChange={(e) => pickService(e.target.value)}
          >
            <option value="">— выбрать сервис —</option>
            {serviceOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          {!mockMode &&
            !servicesQ.loading &&
            !servicesQ.error &&
            serviceOptions.length === 0 && (
              <div className="text-xs text-dim italic mt-2">
                Нет сервисов с ролевым каталогом.
              </div>
            )}
        </>
      )}

      <div className="text-xs text-dim mb-1 mt-4">Роли:</div>
      {!service ? (
        <div className="text-xs text-dim italic">
          Сначала выбери сервис — тогда подгрузятся его роли.
        </div>
      ) : (
        <>
          {!deptId && (
            <div className="text-xs text-warn italic mb-2">
              У пользователя нет dept_id — каталог ролей сервиса подгрузить
              нельзя. Введи имя роли вручную ниже.
            </div>
          )}
          {deptId && rolesQ.loading && (
            <div className="spinner" aria-label="Загрузка" />
          )}
          {deptId && rolesQ.error && (
            <div className="alert-danger text-xs">{rolesQ.error.message}</div>
          )}
          {available.length > 0 && (
            <div className="flex flex-col gap-1 max-h-[240px] overflow-y-auto border border-token rounded p-2">
              {available.map((r) => (
                <label
                  key={r.role_name}
                  className="flex items-center gap-2 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(r.role_name)}
                    onChange={() => toggle(r.role_name)}
                  />
                  <span className="mono">{r.role_name}</span>
                  {r.is_system && (
                    <span className="badge badge-warn">system</span>
                  )}
                  {r.description && (
                    <span className="text-xs text-dim truncate">
                      {r.description}
                    </span>
                  )}
                </label>
              ))}
            </div>
          )}
          <div className="mt-3">
            <div className="text-xs text-dim mb-1">Добавить вручную:</div>
            <div className="flex items-center gap-2">
              <input
                className="input flex-1"
                value={manual}
                onChange={(e) => setManual(e.target.value)}
                placeholder="role_name"
              />
              <button
                className="btn btn-sm"
                disabled={!manual || selected.includes(manual)}
                onClick={() => {
                  setSelected((cur) => [...cur, manual]);
                  setManual("");
                }}
              >
                <Plus className="w-3 h-3" /> добавить
              </button>
            </div>
          </div>
          {selected.length > 0 && (
            <div className="mt-3">
              <div className="text-xs text-dim mb-1">Будет назначено:</div>
              <div className="flex flex-wrap gap-1">
                {selected.map((r) => (
                  <span
                    key={r}
                    className="badge badge-accent flex items-center gap-1"
                  >
                    {r}
                    <button
                      type="button"
                      className="text-dim hover:text-danger"
                      onClick={() => toggle(r)}
                      aria-label={`убрать ${r}`}
                    >
                      <XCircle className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className="text-xs text-dim mt-3 italic">
            Replace-семантика: переданный список заменит текущий набор ролей
            юзера в этом сервисе целиком.
          </div>
        </>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose} disabled={busy !== null}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={save}
          disabled={!service || busy !== null}
        >
          {busy ? "..." : "Сохранить"}
        </button>
      </div>
    </ModalShell>
  );
}

// Сервисы, к которым нельзя назначить роль (нет ролевого каталога). auth_service
// разложен на отдельные admin-страницы, worker_service ролей не несёт,
// loging_service управляется платформенными ролями (loging_admin / loging_reader),
// а каталожные guest/reader/operator/admin для него инертны.
const NON_ROLE_SERVICES = new Set<string>([
  "auth_service",
  "worker_service",
  "loging_service",
]);

// Fallback для mock-режима, когда `listServices` не дёргается (backend не поднят).
const MOCK_PICKER_SERVICES = [
  "secret_service",
  "server_service",
  "config_service",
  "docker_registry",
];

function GroupsTab({
  userId,
  deptId,
  groups,
  loading,
  err,
  canManage,
  capsReason,
  mockMode,
  run,
  busy,
}: {
  userId: string;
  deptId: string | null;
  groups: Group[];
  loading: boolean;
  err: Error | null;
  canManage: boolean;
  capsReason: string;
  mockMode: boolean;
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>;
  busy: string | null;
}) {
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);

  if (loading) return <div className="spinner" aria-label="Загрузка" />;
  if (err) return <div className="alert-danger">{err.message}</div>;

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold flex items-center gap-2">
          <UsersRound className="w-4 h-4 text-accent" /> Группы · {groups.length}
        </div>
        <button
          className="btn btn-sm flex items-center gap-1"
          disabled={!canManage}
          title={canManage ? "Добавить в группу" : capsReason}
          onClick={() => setAdding(true)}
        >
          <Plus className="w-3 h-3" /> Добавить в группу
        </button>
      </div>
      {groups.length === 0 ? (
        <div className="text-sm text-dim italic">
          Пользователь не состоит ни в одной группе.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">Группа</th>
              <th className="pb-2 pr-3">Отображаемое имя</th>
              <th className="pb-2 pr-3">Отдел</th>
              <th className="pb-2 text-right"></th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id} className="border-t border-token">
                <td className="py-2 pr-3 mono text-xs">{g.name}</td>
                <td className="py-2 pr-3">{g.description ?? "—"}</td>
                <td className="py-2 pr-3 text-xs text-dim">
                  <DeptInline deptId={g.department_id} />
                </td>
                <td className="py-2 text-right">
                  <button
                    className="btn btn-sm btn-danger"
                    disabled={!canManage || busy !== null}
                    title={canManage ? "Убрать из группы" : capsReason}
                    onClick={async () => {
                      if (
                        !(await confirm.confirm({
                          message: `Убрать пользователя из ${g.name}?`,
                          danger: true,
                          confirmLabel: "Убрать",
                        }))
                      )
                        return;
                      run("remove-from-group", () =>
                        removeUserFromGroup(userId, g.id),
                      );
                    }}
                  >
                    <XCircle className="w-3 h-3" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {adding && (
        <AddToGroupModal
          userId={userId}
          deptId={deptId}
          alreadyIn={new Set(groups.map((g) => g.id))}
          onClose={() => setAdding(false)}
          onAdded={() => setAdding(false)}
          mockMode={mockMode}
          run={run}
          busy={busy}
        />
      )}
    </div>
  );
}

function AddToGroupModal({
  userId,
  deptId,
  alreadyIn,
  onClose,
  onAdded,
  mockMode,
  run,
  busy,
}: {
  userId: string;
  deptId: string | null;
  alreadyIn: Set<string>;
  onClose: () => void;
  onAdded: () => void;
  mockMode: boolean;
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>;
  busy: string | null;
}) {
  const allQ = useQuery<{ items: Group[]; total: number }>(
    () => listGroupsWithTotal({ limit: 200 }),
    [],
    { enabled: !mockMode },
  );
  const { depts: deptMap } = useLabelMaps();
  const [pickId, setPickId] = useState<string>("");

  const candidates = (allQ.data?.items ?? []).filter(
    (g) => !alreadyIn.has(g.id),
  );
  // Подсказка: соответствие dept'у юзера сверху списка.
  const sorted = [...candidates].sort((a, b) => {
    if (a.department_id === deptId && b.department_id !== deptId) return -1;
    if (b.department_id === deptId && a.department_id !== deptId) return 1;
    return a.name.localeCompare(b.name);
  });

  async function add() {
    if (!pickId) return;
    await run("add-to-group", () => addUserToGroup(userId, pickId));
    onAdded();
  }

  return (
    <ModalShell title="Добавить в группу" onClose={onClose}>
      {allQ.loading && <div className="spinner" aria-label="Загрузка" />}
      {allQ.error && <div className="alert-danger text-xs">{allQ.error.message}</div>}
      {sorted.length === 0 && !allQ.loading ? (
        <div className="text-sm text-dim italic">
          Нет доступных групп для добавления.
        </div>
      ) : (
        <select
          className="input w-full"
          value={pickId}
          onChange={(e) => setPickId(e.target.value)}
        >
          <option value="">— выбрать группу —</option>
          {sorted.map((g) => {
            const deptLabel = deptMap.get(g.department_id) ?? g.department_id;
            return (
              <option key={g.id} value={g.id}>
                {g.name} ({deptLabel})
                {g.department_id === deptId ? " · свой отдел" : ""}
              </option>
            );
          })}
        </select>
      )}
      <TruncationNotice
        className="mt-2"
        shown={allQ.data?.items.length ?? 0}
        total={allQ.data?.total ?? null}
      />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose} disabled={busy !== null}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={add}
          disabled={!pickId || busy !== null}
        >
          {busy ? "..." : "Добавить"}
        </button>
      </div>
    </ModalShell>
  );
}

function SessionsTab({
  userId,
  canRevoke,
  capsReason,
  mockMode,
}: {
  userId: string;
  canRevoke: boolean;
  capsReason: string;
  mockMode: boolean;
}) {
  const sessQ = useQuery<SessionListResponse>(
    () => listUserSessions(userId),
    [userId],
    { enabled: !mockMode && !!userId },
  );
  const confirm = useConfirm();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function revokeAll() {
    if (mockMode) {
      setInfo("mock: revoke all");
      return;
    }
    if (
      !(await confirm.confirm({
        message: "Завершить ВСЕ сессии этого пользователя?",
        danger: true,
        confirmLabel: "Завершить сессии",
      }))
    )
      return;
    setBusy("all");
    setErr(null);
    setInfo(null);
    try {
      const r = await revokeUserAllSessions(userId);
      setInfo(`Отозвано сессий: ${r.revoked_count}`);
      sessQ.refetch();
    } catch (e) {
      setErr(apiErrMsg(e));
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
      setErr(apiErrMsg(e));
    } finally {
      setBusy(null);
    }
  }

  const sessions = sessQ.data?.items ?? [];

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold flex items-center gap-2">
          <Monitor className="w-4 h-4 text-accent" /> Активные сессии ·{" "}
          {sessions.length}
        </div>
        <button
          className="btn btn-danger flex items-center gap-1"
          onClick={revokeAll}
          disabled={!canRevoke || busy !== null || sessions.length === 0}
          title={canRevoke ? undefined : capsReason}
        >
          <LogOut className="w-4 h-4" /> Завершить все
        </button>
      </div>
      {mockMode && (
        <div className="text-xs text-dim italic">
          mock-режим: список сессий не подгружается.
        </div>
      )}
      {!mockMode && sessQ.loading && (
        <div className="spinner" aria-label="Загрузка" />
      )}
      {!mockMode && sessQ.error && (
        <div className="alert-danger">{sessQ.error.message}</div>
      )}
      {!mockMode && !sessQ.loading && !sessQ.error && sessions.length === 0 && (
        <div className="text-sm text-dim italic">Активных сессий нет.</div>
      )}
      {sessions.length > 0 && (
        <div className="border border-token rounded overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase surface-2">
              <tr>
                <th className="px-3 py-2">session_id</th>
                <th className="px-3 py-2">IP</th>
                <th className="px-3 py-2">UA</th>
                <th className="px-3 py-2">Создана</th>
                <th className="px-3 py-2">Последнее использование</th>
                <th className="px-3 py-2">Истекает</th>
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
                    {fmtTs(s.created_at)}
                  </td>
                  <td className="px-3 py-2 text-xs text-dim mono">
                    {fmtTs(s.last_used_at)}
                  </td>
                  <td className="px-3 py-2 text-xs text-dim mono">
                    {fmtTs(s.expires_at)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={!canRevoke || busy !== null}
                      title={canRevoke ? "Завершить сессию" : capsReason}
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
      {err && <div className="alert-danger">{err}</div>}
      {info && <div className="text-xs text-ok">{info}</div>}
      {!canRevoke && (
        <div className="text-xs text-dim italic">Только чтение: {capsReason}</div>
      )}
    </div>
  );
}

function DangerTab({
  user,
  caps,
  run,
  busy,
  mockMode,
}: {
  user: ApiUser;
  caps: ReturnType<typeof userMutationCaps>;
  run: (label: string, fn: () => Promise<unknown>) => Promise<void>;
  busy: string | null;
  mockMode: boolean;
}) {
  const confirm = useConfirm();
  return (
    <div className="card flex flex-col gap-3">
      <div className="text-sm font-semibold flex items-center gap-2 text-danger">
        <Trash2 className="w-4 h-4" /> Опасная зона
      </div>
      <div className="flex gap-2 flex-wrap items-center">
        {!user.is_banned ? (
          <button
            className="btn btn-danger flex items-center gap-1"
            disabled={!caps.disable || busy !== null}
            title={caps.disable ? undefined : caps.reason}
            onClick={async () => {
              const { ok, reason } = await confirm.prompt({
                title: "Бан пользователя",
                message: `Забанить пользователя ${user.username}? Действие необратимо; для отмены нужен Unban.`,
                reason: true,
                reasonLabel: "Причина бана",
                reasonRequired: true,
                danger: true,
                confirmLabel: "Забанить",
              });
              if (!ok || !reason) return;
              run("ban", () =>
                banUser(user.id, { ban_type: "permanent", reason }),
              );
            }}
          >
            <ShieldOff className="w-4 h-4" /> Забанить
          </button>
        ) : (
          <button
            className="btn flex items-center gap-1"
            disabled={!caps.disable || busy !== null}
            title={caps.disable ? undefined : caps.reason}
            onClick={async () => {
              if (
                !(await confirm.confirm({
                  message: `Разбанить пользователя ${user.username}?`,
                  confirmLabel: "Разбанить",
                }))
              )
                return;
              run("unban", () => unbanUser(user.id));
            }}
          >
            <ShieldCheck className="w-4 h-4" /> Разбанить
          </button>
        )}
        <button
          className="btn btn-danger-solid flex items-center gap-1"
          disabled={!caps.delete || busy !== null}
          title={caps.delete ? undefined : caps.reason}
          onClick={async () => {
            const { ok, reason } = await confirm.prompt({
              title: "Удаление пользователя",
              message: `Удалить пользователя ${user.username} полностью? Действие необратимо: исчезнут сессии, PAT-токены, привязки к группам.`,
              reason: true,
              reasonLabel: "Причина удаления (Q3 reorg / left / ...)",
              reasonRequired: true,
              danger: true,
              confirmLabel: "Удалить пользователя",
            });
            if (!ok || !reason) return;
            run("delete", () => deleteUser(user.id, { reason }));
          }}
        >
          <Trash2 className="w-4 h-4" /> Удалить пользователя
        </button>
        <span className="text-xs text-dim flex items-center gap-1 ml-auto">
          <AlertTriangle className="w-3 h-3 text-warn" />
          необратимые операции, попадают в audit как CRITICAL
        </span>
      </div>
      {mockMode && (
        <div className="text-xs text-dim italic">
          mock-режим: операции не уходят на backend.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.4)" }}
      onClick={onClose}
    >
      <div
        className="surface border border-token rounded-lg p-5 max-w-lg w-full mx-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-semibold">{title}</h4>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            <XCircle className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

const fmtTs = formatMskShort;

function DeptInline({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span>{label}</span>;
}

function UserDeptLabel({
  name,
  id,
}: {
  name?: string | null;
  id?: string | null;
}) {
  const fromMap = useDeptLabel(id);
  if (name) return <span>{name}</span>;
  if (!id) return <>— (платформенный)</>;
  return <span>{fromMap}</span>;
}

function ServiceInline({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}
