import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ShieldCheck,
  Edit3,
  Trash2,
  UserPlus,
  UserMinus,
  Lock,
  X,
  Search,
  Users,
  UsersRound,
  type LucideIcon,
} from "lucide-react";
import {
  SERVER_ROLES,
  SECRET_ROLES,
  WORKER_ROLES,
  type ServiceRoleDef,
} from "@/mocks/cluster";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import type { PersonaId } from "@/types/persona";
import {
  InlineEditor,
  FormRow,
  LogingPlatformRoleBanner,
  NotWiredInline,
  StatRow,
  useInlineState,
} from "./_inline";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import { formatMsk } from "@/lib/datetime";
import { listDepartments } from "@/api/auth/departments";
import {
  listServiceRoles,
  createServiceRole,
  patchServiceRole,
  deleteServiceRole,
  bulkAssignServiceRole,
  bulkRevokeServiceRole,
} from "@/api/auth/service_roles";
import { listUsers } from "@/api/auth/users";
import {
  listGroupsWithTotal,
  listGroupMembers,
} from "@/api/auth/groups";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import type {
  Department,
  Group,
  ServiceRole,
  User,
} from "@/api/auth/types";
import { personaDeptId, isPlatformWideAdmin, isDepAdmin } from "@/lib/rbac";
import { useDeptLabel, useServiceLabel } from "@/lib/labels";
import { useTimeoutRef } from "@/lib/useTimeoutRef";

/**
 * Mock-режим: какие фиктивные ролевые наборы и какие persona-id'шники имеют
 * право редактировать роли — для каждого из четырёх «исторических» сервисов.
 * Для произвольного нового сервиса записи нет: mock-вкладка показывает пустой
 * каталог + дефолтный editorIds (bob), что нормально для скриншотов.
 */
const MOCK_FALLBACK_EDITORS: PersonaId[] = ["bob"];

interface MockServiceMeta {
  roles: ServiceRoleDef[];
  editors: PersonaId[];
  note?: string;
}

const MOCK_SERVICE_META: Record<string, MockServiceMeta> = {
  server_service: {
    roles: SERVER_ROLES,
    editors: ["bob", "pavel"],
    note: "Роли назначаются per-resource: read / write / rotate на уровне сервера или группы.",
  },
  secret_service: {
    roles: SECRET_ROLES,
    editors: ["bob", "igor", "pavel"],
    note: "reveal — отдельная роль: каждое использование пишется в audit с reason.",
  },
  loging_service: {
    // loging_service сервис-ролей не несёт — управляется платформенными
    // loging_admin / loging_reader. Карточка для него и так отдаёт баннер
    // ранним return'ом, так что каталог пустой.
    roles: [],
    editors: ["bob", "carol"],
    note: "logging_reader не правит правила/retention — только смотрит события.",
  },
  server_worker: {
    roles: WORKER_ROLES,
    editors: ["bob", "pavel"],
    note: "DLQ-purge — только worker.admin · audit-event на каждое сообщение.",
  },
};

interface Props {
  /**
   * Backend `service_name` напрямую (`server_service` / `secret_service` /
   * `loging_service` / любой кастомный сервис из `GET /services`).
   * Используется как path-сегмент в URL ролевых endpoint'ов.
   */
  serviceName: string;
  title: string;
  note?: string;
  icon?: LucideIcon;
}

/**
 * Shared service-role inline editor (mock + live).
 *
 * Live mode pulls roles for `(department_id, service_name)`:
 *   - `account_admin` chooses the department via a dropdown above the list
 *     (default = first department from `GET /departments`);
 *   - `dep_admin` is locked to their own dept (`persona.dept_id`);
 *   - other personas get a read-only `NotWiredInline` placeholder.
 *
 * Mock mode renders the legacy hard-coded catalogue so screenshots / tests
 * keep working without an auth backend.
 */
export function ServiceRolesCard({
  serviceName,
  title,
  note,
  icon: Icon = ShieldCheck,
}: Props) {
  const mockMode = useMockMode();
  // loging_service гейтится только platform_role'ами; service-роли в этой
  // связке guard'ами игнорируются. Скрываем CRUD во всех режимах.
  if (serviceName === "loging_service") {
    return (
      <div className="card m-4 flex flex-col gap-3 w-full">
        <h3 className="font-semibold flex items-center gap-2">
          <Icon className="w-4 h-4 text-accent" /> {title}
        </h3>
        <LogingPlatformRoleBanner />
      </div>
    );
  }
  if (mockMode) {
    const meta = MOCK_SERVICE_META[serviceName];
    return (
      <MockServiceRolesCard
        title={title}
        roles={meta?.roles ?? []}
        note={note ?? meta?.note}
        icon={Icon}
        editorIds={meta?.editors ?? MOCK_FALLBACK_EDITORS}
      />
    );
  }
  return (
    <LiveServiceRolesCard
      serviceName={serviceName}
      title={title}
      note={note}
      icon={Icon}
    />
  );
}

// ---------------------------------------------------------------------------
// Live mode
// ---------------------------------------------------------------------------

function LiveServiceRolesCard({
  serviceName,
  title,
  note,
  icon: Icon,
}: {
  serviceName: string;
  title: string;
  note?: string;
  icon: LucideIcon;
}) {
  const { persona } = usePersona();
  const toast = useToast();
  const confirm = useConfirm();
  const backendServiceName = serviceName;

  const platformAdmin = isPlatformWideAdmin(persona);
  const depAdmin = isDepAdmin(persona);
  const canEdit = platformAdmin || depAdmin;

  // No platform/dep-admin role → нет прав на CRUD ролей в `(dept, svc)`.
  // Backend ответит 403, поэтому даже листинг скрываем.
  if (!canEdit) {
    return (
      <NotWiredInline
        service={title}
        endpoints={[
          `GET   /auth/v1/departments/{department_id}/services/${backendServiceName}/roles`,
          `POST  /auth/v1/departments/{department_id}/services/${backendServiceName}/roles`,
          `PATCH .../roles/{role_name}`,
          `POST  .../roles/{role_name}/assign | /revoke`,
        ]}
      />
    );
  }

  // department_id selector — `account_admin` picks from list, `dep_admin`
  // is pinned to own dept. Если в URL пришёл `?dept_id=<id>` (из навигации
  // со страницы /admin/services.departments) — берём его как hint для
  // первичного выбора.
  const myDept = personaDeptId(persona);
  const [searchParams] = useSearchParams();
  const hintedDept = searchParams.get("dept_id");
  const deptsQ = useQuery<Department[]>(
    () => listDepartments(),
    [],
    { enabled: platformAdmin },
  );
  const [deptId, setDeptId] = useState<string | null>(
    depAdmin ? myDept : hintedDept,
  );
  // When deps load, auto-pick: hinted → persona dept → first.
  useEffect(() => {
    if (!platformAdmin) return;
    if (deptId) return;
    const list = deptsQ.data ?? [];
    if (list.length === 0) return;
    const preferred =
      (hintedDept && list.find((d) => d.id === hintedDept)) ||
      (myDept ? list.find((d) => d.id === myDept) : undefined);
    setDeptId(preferred?.id ?? list[0].id);
  }, [platformAdmin, deptId, deptsQ.data, myDept, hintedDept]);

  const [refreshTick, setRefreshTick] = useState(0);
  const [pending, setPending] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const rolesQ = useQuery<ServiceRole[]>(
    () => listServiceRoles(deptId!, backendServiceName),
    [deptId, backendServiceName, refreshTick],
    { enabled: !!deptId },
  );

  const refetch = () => setRefreshTick((t) => t + 1);
  const run = async (fn: () => Promise<unknown>, successMsg?: string) => {
    setActionErr(null);
    setPending(true);
    try {
      await fn();
      if (successMsg) toast.success(successMsg);
      refetch();
    } catch (e) {
      let msg: string;
      if (e instanceof ApiError) msg = `${e.errorCode}: ${e.message}`;
      else if (e instanceof Error) msg = e.message;
      else msg = String(e);
      setActionErr(msg);
      toast.error(msg);
    } finally {
      setPending(false);
    }
  };

  const items: ServiceRole[] = rolesQ.data ?? [];

  // Header dropdown for dept selection (account_admin only).
  const listHeader = (
    <div className="flex flex-col gap-2">
      {platformAdmin ? (
        <label className="flex items-center gap-2 text-xs">
          <span className="text-dim">dept</span>
          <select
            className="input flex-1"
            value={deptId ?? ""}
            onChange={(e) => setDeptId(e.target.value || null)}
            disabled={deptsQ.loading || (deptsQ.data?.length ?? 0) === 0}
          >
            {(deptsQ.data ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.id})
              </option>
            ))}
            {(deptsQ.data ?? []).length === 0 && (
              <option value="">— нет отделов —</option>
            )}
          </select>
        </label>
      ) : (
        <div className="flex items-center gap-2 text-xs">
          <Lock className="w-3 h-3 text-dim" />
          <span className="text-dim">dept</span>
          <span className="mono">{deptId ?? "—"}</span>
        </div>
      )}
      {actionErr && <div className="alert-danger text-xs">{actionErr}</div>}
      {rolesQ.error && (
        <div className="alert-danger text-xs">
          {rolesQ.error instanceof ApiError
            ? `${rolesQ.error.errorCode}: ${rolesQ.error.message}`
            : rolesQ.error.message}
        </div>
      )}
    </div>
  );

  // No dept context → show empty scaffold with hint, no list query.
  if (!deptId) {
    return (
      <InlineEditor
        title={title}
        icon={Icon}
        hint={`live · backend=${backendServiceName}`}
        items={[]}
        getId={() => ""}
        canEdit={false}
        readonlyNote={note}
        listHeader={listHeader}
        emptyHint={
          deptsQ.loading
            ? "Загрузка списка отделов…"
            : "Выберите отдел сверху, чтобы увидеть роли."
        }
        renderRow={() => null}
        renderDetail={() => null}
      />
    );
  }

  return (
    <InlineEditor
      title={title}
      icon={Icon}
      hint={`live · backend=${backendServiceName} · dept=${deptId}`}
      items={items}
      getId={(r) => r.role_name}
      canEdit={canEdit}
      readonlyNote={canEdit ? undefined : note}
      listHeader={listHeader}
      emptyHint={
        rolesQ.loading
          ? "Загрузка ролей…"
          : `В scope (${deptId}, ${backendServiceName}) ролей нет.`
      }
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.role_name}</div>
              {(item.description || item.is_system) && (
                <div className="text-[11px] text-dim truncate">
                  {item.description}
                  {item.is_system && " · system"}
                </div>
              )}
            </div>
          </div>
        </button>
      )}
      renderDetail={(r, { editing, onClose }) =>
        editing ? (
          <LiveRoleForm
            mode="edit"
            initial={r}
            pending={pending}
            onSubmit={async (body) => {
              await run(
                () =>
                  patchServiceRole(deptId, backendServiceName, r.role_name, body),
                "Роль обновлена",
              );
              onClose();
            }}
            onCancel={onClose}
          />
        ) : (
          <LiveRoleView
            role={r}
            pending={pending}
            canEdit={canEdit}
            onDelete={async () => {
              if (
                !(await confirm.confirm({
                  message: `Удалить роль ${r.role_name}?`,
                  danger: true,
                  confirmLabel: "Удалить",
                }))
              )
                return;
              await run(
                () => deleteServiceRole(deptId, backendServiceName, r.role_name),
                "Роль удалена",
              );
              onClose();
            }}
            onAssign={async (userIds) => {
              await run(
                () =>
                  bulkAssignServiceRole(deptId, backendServiceName, r.role_name, {
                    user_ids: userIds,
                  }),
                "Назначено",
              );
            }}
            onRevoke={async (userIds) => {
              await run(
                () =>
                  bulkRevokeServiceRole(deptId, backendServiceName, r.role_name, {
                    user_ids: userIds,
                  }),
                "Отозвано",
              );
            }}
          />
        )
      }
      renderCreate={
        canEdit
          ? (onClose) => (
              <LiveRoleForm
                mode="new"
                pending={pending}
                onSubmit={async (body) => {
                  if (!body.role_name) return;
                  await run(
                    () =>
                      createServiceRole(deptId, backendServiceName, {
                        role_name: body.role_name!,
                        description: body.description,
                      }),
                    "Роль создана",
                  );
                  onClose();
                }}
                onCancel={onClose}
              />
            )
          : undefined
      }
    />
  );
}

function LiveRoleView({
  role,
  pending,
  canEdit,
  onDelete,
  onAssign,
  onRevoke,
}: {
  role: ServiceRole;
  pending: boolean;
  canEdit: boolean;
  onDelete: () => void | Promise<void>;
  onAssign: (userIds: string[]) => void | Promise<void>;
  onRevoke: (userIds: string[]) => void | Promise<void>;
}) {
  const { startEdit } = useInlineState();
  const lockedReason = role.is_system
    ? "Системную роль нельзя менять / удалять"
    : undefined;

  return (
    <div className="card w-full">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <ShieldCheck className="w-4 h-4 text-accent" /> {role.role_name}
          {role.is_system ? (
            <span className="badge">system</span>
          ) : (
            <span className="badge badge-accent">custom</span>
          )}
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button
              className="btn flex items-center gap-1"
              disabled={role.is_system || pending}
              title={lockedReason}
              onClick={() => startEdit(role.role_name)}
            >
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={role.is_system || pending}
              title={lockedReason}
              onClick={onDelete}
            >
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="role_name" v={<span className="mono">{role.role_name}</span>} />
      <StatRow k="description" v={role.description ?? "—"} />
      <StatRow k="department" v={<RoleDeptLabel deptId={role.department_id} />} />
      <StatRow k="service" v={<RoleServiceLabel name={role.service_name} />} />
      <StatRow k="is_system" v={role.is_system ? "true" : "false"} />
      <StatRow k="created_at" v={<span className="mono">{formatMsk(role.created_at)}</span>} />

      {canEdit && (
        <BulkAssignSection
          roleDeptId={role.department_id}
          pending={pending}
          onAssign={onAssign}
          onRevoke={onRevoke}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bulk assign / revoke — searchable multi-select for users + groups.
// ---------------------------------------------------------------------------

const BULK_MAX = 200;

function BulkAssignSection({
  roleDeptId,
  pending,
  onAssign,
  onRevoke,
}: {
  roleDeptId: string;
  pending: boolean;
  onAssign: (userIds: string[]) => void | Promise<void>;
  onRevoke: (userIds: string[]) => void | Promise<void>;
}) {
  const toast = useToast();
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(new Set());
  const [selectedGroupIds, setSelectedGroupIds] = useState<Set<string>>(new Set());
  const [expanding, setExpanding] = useState(false);

  const usersQ = useQuery<{ items: User[]; total: number }>(
    () => listUsers({ limit: 200 }),
    [],
  );
  const groupsQ = useQuery<{ items: Group[]; total: number }>(
    () => listGroupsWithTotal({ limit: 200 }),
    [],
  );

  // Department filter: backend rejects users from a different dept;
  // platform-wide users (department_id === null) are excluded by default —
  // bulk endpoint accepts только юзеров своего отдела.
  const usersInDept = useMemo<User[]>(
    () =>
      (usersQ.data?.items ?? []).filter(
        (u) => u.department_id === roleDeptId,
      ),
    [usersQ.data, roleDeptId],
  );
  const groupsInDept = useMemo<Group[]>(
    () =>
      (groupsQ.data?.items ?? []).filter((g) => g.department_id === roleDeptId),
    [groupsQ.data, roleDeptId],
  );

  const groupById = useMemo(() => {
    const map = new Map<string, Group>();
    for (const g of groupsInDept) map.set(g.id, g);
    return map;
  }, [groupsInDept]);

  const selectedGroupsLabels = useMemo(
    () =>
      Array.from(selectedGroupIds)
        .map((id) => groupById.get(id))
        .filter((g): g is Group => !!g)
        .map((g) => g.name),
    [selectedGroupIds, groupById],
  );

  // Expand groups → user_ids, merged with direct selection and deduped.
  const resolveEffectiveUserIds = async (): Promise<{
    ids: string[];
    direct: number;
    viaGroups: number;
    emptyGroups: Group[];
  }> => {
    const set = new Set<string>(selectedUserIds);
    const directCount = set.size;
    const emptyGroups: Group[] = [];
    for (const gid of selectedGroupIds) {
      try {
        const members = await listGroupMembers(gid);
        if (members.length === 0) {
          const g = groupById.get(gid);
          if (g) emptyGroups.push(g);
        }
        for (const m of members) set.add(m.user_id);
      } catch (e) {
        const g = groupById.get(gid);
        const label = g ? g.name : gid;
        const msg =
          e instanceof ApiError
            ? `${e.errorCode}: ${e.message}`
            : e instanceof Error
              ? e.message
              : String(e);
        toast.error(`Не удалось получить участников группы ${label}: ${msg}`);
        throw e;
      }
    }
    const ids = Array.from(set);
    const viaGroups = ids.length - directCount;
    return { ids, direct: directCount, viaGroups, emptyGroups };
  };

  const submit = async (action: "assign" | "revoke") => {
    if (selectedUserIds.size === 0 && selectedGroupIds.size === 0) return;
    setExpanding(true);
    let resolved: Awaited<ReturnType<typeof resolveEffectiveUserIds>>;
    try {
      resolved = await resolveEffectiveUserIds();
    } catch {
      setExpanding(false);
      return;
    }
    setExpanding(false);

    if (resolved.ids.length === 0) {
      toast.warn("Никого не затронуто: выбранные группы не содержат участников.");
      return;
    }
    if (resolved.ids.length > BULK_MAX) {
      toast.error(
        `Превышен лимит bulk: ${resolved.ids.length} юзеров, максимум ${BULK_MAX}. Разбей на части.`,
      );
      return;
    }
    if (resolved.emptyGroups.length > 0) {
      const names = resolved.emptyGroups
        .map((g) => g.name)
        .join(", ");
      toast.warn(`Пустые группы пропущены: ${names}`);
    }
    const fn = action === "assign" ? onAssign : onRevoke;
    await fn(resolved.ids);
    setSelectedUserIds(new Set());
    setSelectedGroupIds(new Set());
  };

  const directCount = selectedUserIds.size;
  const groupsCount = selectedGroupIds.size;
  const hasSelection = directCount > 0 || groupsCount > 0;
  const busy = pending || expanding;

  return (
    <div className="mt-4 border-t border-token pt-3 flex flex-col gap-3">
      <div className="text-xs uppercase text-dim">Bulk assign / revoke</div>

      <SearchableMultiSelect<User>
        label="Пользователи"
        icon={Users}
        items={usersInDept}
        loading={usersQ.loading}
        error={
          usersQ.error
            ? usersQ.error instanceof ApiError
              ? `${usersQ.error.errorCode}: ${usersQ.error.message}`
              : usersQ.error.message
            : null
        }
        getId={(u) => u.id}
        getPrimary={(u) => u.username}
        getSecondary={(u) => u.email ?? ""}
        searchableText={(u) =>
          `${u.username} ${u.email ?? ""} ${u.id}`.toLowerCase()
        }
        selected={selectedUserIds}
        onChange={setSelectedUserIds}
        placeholder="Поиск по username / email / id…"
        emptyHint={`Нет пользователей в этом отделе (${roleDeptId}).`}
        sourceShown={usersQ.data?.items.length ?? 0}
        sourceTotal={usersQ.data?.total ?? null}
      />

      <SearchableMultiSelect<Group>
        label="Группы"
        icon={UsersRound}
        items={groupsInDept}
        loading={groupsQ.loading}
        error={
          groupsQ.error
            ? groupsQ.error instanceof ApiError
              ? `${groupsQ.error.errorCode}: ${groupsQ.error.message}`
              : groupsQ.error.message
            : null
        }
        getId={(g) => g.id}
        getPrimary={(g) => g.name}
        getSecondary={(g) => g.id}
        searchableText={(g) =>
          `${g.name} ${g.id}`.toLowerCase()
        }
        selected={selectedGroupIds}
        onChange={setSelectedGroupIds}
        placeholder="Поиск по имени группы…"
        emptyHint={`Нет групп в этом отделе (${roleDeptId}).`}
        sourceShown={groupsQ.data?.items.length ?? 0}
        sourceTotal={groupsQ.data?.total ?? null}
      />

      <div className="text-[11px] text-dim">
        {hasSelection ? (
          <>
            Выбрано: <span className="mono">{directCount}</span> юзеров напрямую
            {groupsCount > 0 && (
              <>
                {" + "}
                <span className="mono">{groupsCount}</span> групп
                {selectedGroupsLabels.length > 0 && (
                  <> ({selectedGroupsLabels.join(", ")})</>
                )}
              </>
            )}
            . Точное число будет посчитано перед запросом (дедуп по user_id).
          </>
        ) : (
          "Выберите пользователей и/или группы выше."
        )}
      </div>

      <div className="flex gap-2">
        <button
          className="btn btn-primary flex items-center gap-1"
          disabled={busy || !hasSelection}
          onClick={() => void submit("assign")}
        >
          <UserPlus className="w-4 h-4" /> assign
        </button>
        <button
          className="btn btn-danger flex items-center gap-1"
          disabled={busy || !hasSelection}
          onClick={() => void submit("revoke")}
        >
          <UserMinus className="w-4 h-4" /> revoke
        </button>
        {hasSelection && (
          <button
            className="btn flex items-center gap-1"
            disabled={busy}
            onClick={() => {
              setSelectedUserIds(new Set());
              setSelectedGroupIds(new Set());
            }}
          >
            <X className="w-4 h-4" /> очистить выбор
          </button>
        )}
      </div>

      <div className="text-[11px] text-dim italic">
        Backend bulk endpoint принимает только user_ids; группы раскрываются на
        клиенте через listGroupMembers и дедуплицируются с прямыми выборами.
        Боты получают доступ через allowed_services, не через service-роль.
      </div>
    </div>
  );
}

function SearchableMultiSelect<T>({
  label,
  icon: Icon,
  items,
  loading,
  error,
  getId,
  getPrimary,
  getSecondary,
  searchableText,
  selected,
  onChange,
  placeholder,
  emptyHint,
  sourceShown,
  sourceTotal,
}: {
  label: string;
  icon: LucideIcon;
  items: T[];
  loading: boolean;
  error: string | null;
  getId: (item: T) => string;
  getPrimary: (item: T) => string;
  getSecondary?: (item: T) => string;
  searchableText: (item: T) => string;
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  placeholder: string;
  emptyHint: string;
  /**
   * Сколько строк реально прилетело в исходной (до dept-фильтра) выдаче и
   * сколько их всего на бэкенде. Источник тянется с капом limit=200, поэтому
   * при `shown < total` часть юзеров/групп отдела может не попасть в picker —
   * показываем честный баннер вместо тихого усечения.
   */
  sourceShown?: number;
  sourceTotal?: number | null;
}) {
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState(false);
  const setCloseTimeout = useTimeoutRef();

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => searchableText(it).includes(q));
  }, [filter, items, searchableText]);

  const selectedItems = useMemo(
    () => items.filter((it) => selected.has(getId(it))),
    [items, selected, getId],
  );

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  };

  const removeChip = (id: string) => {
    const next = new Set(selected);
    next.delete(id);
    onChange(next);
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs flex items-center gap-1">
          <Icon className="w-3.5 h-3.5 text-dim" />
          <span className="font-semibold">{label}</span>
          <span className="text-dim">
            ({selected.size}/{items.length})
          </span>
        </div>
        {selected.size > 0 && (
          <button
            className="text-[11px] text-dim hover:text-accent"
            onClick={() => onChange(new Set())}
            type="button"
          >
            очистить
          </button>
        )}
      </div>

      {selectedItems.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {selectedItems.map((it) => {
            const id = getId(it);
            return (
              <span
                key={id}
                className="chip-small flex items-center gap-1"
                title={id}
              >
                <span className="truncate max-w-[14rem]">{getPrimary(it)}</span>
                <button
                  type="button"
                  className="text-dim hover:text-danger"
                  onClick={() => removeChip(id)}
                  aria-label={`Убрать ${getPrimary(it)}`}
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            );
          })}
        </div>
      )}

      <div className="relative">
        <div className="flex items-center gap-1">
          <Search className="w-3.5 h-3.5 text-dim absolute left-2 pointer-events-none" />
          <input
            className="input w-full pl-7"
            placeholder={placeholder}
            value={filter}
            onFocus={() => setOpen(true)}
            onChange={(e) => {
              setFilter(e.target.value);
              setOpen(true);
            }}
            onBlur={() => {
              // delay so item-click registers
              setCloseTimeout(() => setOpen(false), 150);
            }}
          />
        </div>

        {open && (
          <div className="absolute z-10 left-0 right-0 mt-1 card max-h-56 overflow-auto p-1">
            {loading ? (
              <div className="text-xs text-dim p-2">Загрузка…</div>
            ) : error ? (
              <div className="alert-danger text-xs m-1">{error}</div>
            ) : items.length === 0 ? (
              <div className="text-xs text-dim p-2 italic">{emptyHint}</div>
            ) : filtered.length === 0 ? (
              <div className="text-xs text-dim p-2 italic">
                Ничего не найдено по «{filter}».
              </div>
            ) : (
              filtered.map((it) => {
                const id = getId(it);
                const isSelected = selected.has(id);
                return (
                  <button
                    key={id}
                    type="button"
                    className={`cred-row text-left w-full ${isSelected ? "active" : ""}`}
                    onMouseDown={(e) => {
                      // mousedown fires before blur — keeps dropdown open
                      e.preventDefault();
                      toggle(id);
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        readOnly
                        checked={isSelected}
                        className="pointer-events-none"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm truncate">{getPrimary(it)}</div>
                        {getSecondary && (
                          <div className="text-[11px] text-dim truncate mono">
                            {getSecondary(it)}
                          </div>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>

      <TruncationNotice
        shown={sourceShown ?? items.length}
        total={sourceTotal ?? null}
      />
    </div>
  );
}

interface RoleFormBody {
  role_name?: string;
  description?: string;
}

function LiveRoleForm({
  initial,
  mode,
  pending,
  onSubmit,
  onCancel,
}: {
  initial?: ServiceRole;
  mode: "new" | "edit";
  pending: boolean;
  onSubmit: (body: RoleFormBody) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [roleName, setRoleName] = useState(initial?.role_name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");

  const submit = () => {
    if (mode === "new") {
      void onSubmit({
        role_name: roleName.trim(),
        description: description.trim() || undefined,
      });
    } else {
      // PATCH — отправляем только description; role_name неизменяем.
      void onSubmit({
        description: description.trim() || undefined,
      });
    }
  };

  const submitDisabled = pending
    || (mode === "new" && !roleName.trim());

  return (
    <div className="card w-full">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая роль" : `Edit · ${initial?.role_name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow
          label="role_name"
          hint="например server.deploy или secret.rotator · неизменяемо после создания"
        >
          <input
            className="input mono"
            value={roleName}
            disabled={mode === "edit"}
            onChange={(e) => setRoleName(e.target.value)}
          />
        </FormRow>
        <FormRow label="description">
          <textarea
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormRow>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onCancel} disabled={pending}>
          Отмена
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={submitDisabled}>
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mock mode — legacy behaviour preserved for screenshots / tests.
// ---------------------------------------------------------------------------

const SERVICE_ROLES_SCOPE_MISSING =
  "Mock-режим · реальный CRUD доступен только в live (VITE_USE_MOCK_AUTH=false)";

function MockServiceRolesCard({
  title,
  roles,
  note,
  icon: Icon,
  editorIds,
}: {
  title: string;
  roles: ServiceRoleDef[];
  note?: string;
  icon: LucideIcon;
  editorIds: PersonaId[];
}) {
  const { persona } = usePersona();
  const canEdit = editorIds.includes(persona.id);

  return (
    <InlineEditor
      title={title}
      icon={Icon}
      hint="набор ролей сервиса · назначение per-resource или per-dept"
      items={roles}
      getId={(r) => r.id}
      canEdit={canEdit}
      readonlyNote={canEdit ? undefined : note}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.name}</div>
              <div className="text-[11px] text-dim truncate">
                {item.level} · {item.assigned} assigned
              </div>
            </div>
          </div>
        </button>
      )}
      renderDetail={(r, { editing, onClose }) => {
        if (editing) return <MockRoleForm initial={r} onDone={onClose} mode="edit" />;
        return <MockRoleView role={r} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <MockRoleForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function MockRoleView({ role, canEdit }: { role: ServiceRoleDef; canEdit: boolean }) {
  const { startEdit } = useInlineState();
  const toast = useToast();
  return (
    <div className="card w-full">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <ShieldCheck className="w-4 h-4 text-accent" /> {role.name}
          <span className="badge">{role.level}</span>
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(role.id)}>
              <Edit3 className="w-4 h-4" /> Edit
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              onClick={() => toast.warn(SERVICE_ROLES_SCOPE_MISSING)}
            >
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>
      <StatRow k="role_id" v={<span className="mono">{role.id}</span>} />
      <StatRow k="name" v={<span className="mono">{role.name}</span>} />
      <StatRow k="level" v={role.level} />
      <StatRow k="description" v={role.description} />
      <StatRow k="assigned" v={<span className="mono">{role.assigned}</span>} />
    </div>
  );
}

function MockRoleForm({
  initial,
  onDone,
  mode,
}: {
  initial?: ServiceRoleDef;
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [level, setLevel] = useState<ServiceRoleDef["level"]>(initial?.level ?? "admin");
  const [description, setDescription] = useState(initial?.description ?? "");
  const toast = useToast();
  const submit = () => {
    toast.warn(SERVICE_ROLES_SCOPE_MISSING);
    onDone();
  };

  return (
    <div className="card w-full">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая роль" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name" hint="например server.deploy или secret.rotator">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="level">
          <select className="input" value={level} onChange={(e) => setLevel(e.target.value as ServiceRoleDef["level"])}>
            <option value="admin">admin</option>
            <option value="rotator">rotator</option>
          </select>
        </FormRow>
        <FormRow label="description">
          <textarea className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormRow>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>Отмена</button>
        <button className="btn btn-primary" onClick={submit}>
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

function RoleDeptLabel({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span>{label}</span>;
}

function RoleServiceLabel({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}
