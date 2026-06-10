import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ShieldCheck, Edit3, Trash2, UserPlus, UserMinus, Lock, type LucideIcon } from "lucide-react";
import {
  SERVER_ROLES,
  SECRET_ROLES,
  LOGING_ROLES,
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
import { listDepartments } from "@/api/auth/departments";
import {
  listServiceRoles,
  createServiceRole,
  patchServiceRole,
  deleteServiceRole,
  bulkAssignServiceRole,
  bulkRevokeServiceRole,
} from "@/api/auth/service_roles";
import type {
  Department,
  ServiceRole,
} from "@/api/auth/types";
import { personaDeptId, isPlatformWideAdmin, isDepAdmin } from "@/lib/rbac";

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
    roles: LOGING_ROLES,
    editors: ["bob", "carol"],
    note: "logging_reader не правит правила/retention — только смотрит события.",
  },
  worker_service: {
    roles: WORKER_ROLES,
    editors: ["bob", "pavel"],
    note: "DLQ-purge — только worker.admin · audit-event на каждое сообщение.",
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
   * `loging_service` / `worker_service` / любой кастомный сервис из
   * `GET /services`). Используется как path-сегмент в URL ролевых endpoint'ов.
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
      <div className="card max-w-3xl m-4 flex flex-col gap-3">
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
                {d.display_name} ({d.id})
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
              <div className="text-[11px] text-dim truncate">
                {item.display_name}
                {item.is_system && " · system"}
              </div>
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
              if (!window.confirm(`Удалить роль ${r.role_name}?`)) return;
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
                  if (!body.role_name || !body.display_name) return;
                  await run(
                    () =>
                      createServiceRole(deptId, backendServiceName, {
                        role_name: body.role_name!,
                        display_name: body.display_name!,
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
  const [assignInput, setAssignInput] = useState("");
  const lockedReason = role.is_system
    ? "Системную роль нельзя менять / удалять"
    : undefined;

  const parseIds = () =>
    assignInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

  return (
    <div className="card max-w-2xl">
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
      <StatRow k="display_name" v={role.display_name} />
      <StatRow k="description" v={role.description ?? "—"} />
      <StatRow k="department_id" v={<span className="mono">{role.department_id}</span>} />
      <StatRow k="service_name" v={<span className="mono">{role.service_name}</span>} />
      <StatRow k="is_system" v={role.is_system ? "true" : "false"} />
      <StatRow k="created_at" v={<span className="mono">{role.created_at}</span>} />

      {canEdit && (
        <div className="mt-4 border-t border-token pt-3">
          <div className="text-xs uppercase text-dim mb-2">Bulk assign / revoke</div>
          <input
            className="input mono w-full"
            placeholder="user_ids csv (usr_a, usr_b)"
            value={assignInput}
            onChange={(e) => setAssignInput(e.target.value)}
          />
          <div className="flex gap-2 mt-2">
            <button
              className="btn btn-primary flex items-center gap-1"
              disabled={pending || parseIds().length === 0}
              onClick={() => {
                const ids = parseIds();
                if (ids.length === 0) return;
                void onAssign(ids);
              }}
            >
              <UserPlus className="w-4 h-4" /> assign
            </button>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={pending || parseIds().length === 0}
              onClick={() => {
                const ids = parseIds();
                if (ids.length === 0) return;
                void onRevoke(ids);
              }}
            >
              <UserMinus className="w-4 h-4" /> revoke
            </button>
          </div>
          <div className="text-[11px] text-dim mt-1 italic">
            Backend bulk endpoint принимает только user_ids (ботам роль выдаётся через
            allowed_services боту, не через service-роль).
          </div>
        </div>
      )}
    </div>
  );
}

interface RoleFormBody {
  role_name?: string;
  display_name?: string;
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
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");

  const submit = () => {
    if (mode === "new") {
      void onSubmit({
        role_name: roleName.trim(),
        display_name: displayName.trim(),
        description: description.trim() || undefined,
      });
    } else {
      // PATCH — отправляем только display_name / description; role_name неизменяем.
      void onSubmit({
        display_name: displayName.trim(),
        description: description.trim() || undefined,
      });
    }
  };

  const submitDisabled = pending
    || (mode === "new" && (!roleName.trim() || !displayName.trim()))
    || (mode === "edit" && !displayName.trim());

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая роль" : `Edit · ${initial?.role_name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow
          label="role_name"
          hint="например server.operator или secret.rotator · неизменяемо после создания"
        >
          <input
            className="input mono"
            value={roleName}
            disabled={mode === "edit"}
            onChange={(e) => setRoleName(e.target.value)}
          />
        </FormRow>
        <FormRow label="display_name">
          <input
            className="input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
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
    <div className="card max-w-2xl">
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
  const [level, setLevel] = useState<ServiceRoleDef["level"]>(initial?.level ?? "reader");
  const [description, setDescription] = useState(initial?.description ?? "");
  const toast = useToast();
  const submit = () => {
    toast.warn(SERVICE_ROLES_SCOPE_MISSING);
    onDone();
  };

  return (
    <div className="card max-w-2xl">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новая роль" : `Edit · ${initial?.name}`}
      </h3>
      <div className="flex flex-col gap-3">
        <FormRow label="name" hint="например server.operator или secret.rotator">
          <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
        </FormRow>
        <FormRow label="level">
          <select className="input" value={level} onChange={(e) => setLevel(e.target.value as ServiceRoleDef["level"])}>
            <option value="admin">admin</option>
            <option value="operator">operator</option>
            <option value="reader">reader</option>
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
