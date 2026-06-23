import { useCallback, useMemo, useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  Lock,
  Bot,
  Loader2,
  Plus,
  X,
  Grid3x3,
  SlidersHorizontal,
  Trash2,
  Check,
  Pencil,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Tabs } from "@/components/ui/Tabs";
import { useQuery } from "@/api/auth/useQuery";
import {
  getPermissionCatalog,
  listPermissions,
  setPermission,
  deletePermission,
} from "@/api/server/permissions";
import {
  createServiceRole,
  listServiceRoles,
  patchServiceRole,
  deleteServiceRole,
} from "@/api/auth/service_roles";
import { apiErrMsg } from "@/api/client";
import { personaDeptId } from "@/lib/rbac";
import type { ServiceRole } from "@/api/auth/types";
import type {
  ActionName,
  EntityType,
  PermissionCatalogAction,
  PermissionCatalogItem,
  PermissionEntry,
  RoleName,
} from "@/api/server/types";

/**
 * Матрица разрешений `server_service`. Группируется по сущности
 * (`entity_type`); внутри — таблица роли × действия, каждая ячейка — чекбокс,
 * клик по которому мгновенно выдаёт/отзывает grant через PUT/DELETE. Правка
 * оптимистична: галка ставится сразу, при ошибке backend'а откат + тост.
 *
 * Строки `admin` и `guest` залочены: admin держит полный доступ, guest —
 * базовый, их правила менять нельзя (чекбокс disabled). Редактируются
 * `reader`, `operator` и кастомные роли отдела. Кастомную роль можно завести
 * прямо здесь кнопкой «+ Новая роль» — она создаётся в каталоге
 * `(dept, server_service)` auth-сервиса и тут же появляется строкой матрицы.
 *
 * RBAC: матрица — business-data `server_service`. Backend пускает носителя
 * `(permission, *, view/grant/revoke)`: department_admin своего отдела или
 * сервисную роль `server.admin`. Platform-админы режутся middleware'ом 403
 * PLATFORM_ADMIN_BUSINESS_DATA_DENIED — им страница не показывается.
 *
 * Рядом с матрицей — режим «Управление ролью»: выбираешь одну роль отдела и
 * правишь её точечно — права по сущностям×действиям теми же PUT/DELETE,
 * description через PATCH auth-сервиса, удаление кастомной роли через DELETE.
 * Системные `admin`/`guest` нередактируемы, а удалять можно только кастомные
 * (`is_system=false`). Backend rename не поддерживает (PATCH меняет только
 * description), поэтому имя роли остаётся read-only.
 */
export function ServicesServerPermissions() {
  const { persona } = usePersona();
  const canView =
    persona.platform_role === "dep_admin" ||
    persona.service_roles?.server === "admin";
  if (!canView) {
    return (
      <div className="p-8">
        <div className="alert-danger flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          <span>
            403 · матрица разрешений `server_service` доступна
            department_admin своего отдела или роли
            <span className="mono"> server.admin</span>.
          </span>
        </div>
      </div>
    );
  }
  return <ServicesServerPermissionsLive />;
}

// Роли, чьи правила нельзя менять из этого UI.
const LOCKED_ROLES: Record<string, string> = {
  admin: "admin держит полный доступ — правила не редактируются",
  guest: "guest — базовая роль, правила не редактируются",
};

// Системные роли каталога — их нельзя дублировать кастомной.
const SYSTEM_ROLES: RoleName[] = ["guest", "reader", "operator", "admin"];

// Порядок системных ролей в таблице; кастомные идут после по алфавиту.
const SYSTEM_ROLE_ORDER: RoleName[] = ["guest", "reader", "operator", "admin"];

function roleSortKey(role: RoleName): string {
  const idx = SYSTEM_ROLE_ORDER.indexOf(role);
  // Системные роли — фиксированный порядок (0..3), кастомные — после, по имени.
  return idx >= 0 ? `0${idx}` : `1${role}`;
}

type CellStatus = "idle" | "saving";

type ViewMode = "matrix" | "role";

function ServicesServerPermissionsLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const myDept = personaDeptId(persona);

  const [mode, setMode] = useState<ViewMode>("matrix");
  const [refreshTick, setRefreshTick] = useState(0);
  const bump = useCallback(() => setRefreshTick((t) => t + 1), []);

  const catalogQ = useQuery(() => getPermissionCatalog(), []);
  const grantsQ = useQuery(
    () => listPermissions({ describe: true }),
    [refreshTick],
  );
  // Каталог ролей server_service отдела — для режима «Управление ролью».
  // Без отдела (myDept=null) запрос бессмысленен, отдаём пустой список.
  const rolesQ = useQuery(
    () =>
      myDept
        ? listServiceRoles(myDept, "server_service")
        : Promise.resolve([] as ServiceRole[]),
    [myDept, refreshTick],
  );

  const catalog = catalogQ.data ?? [];
  const grants = grantsQ.data?.items ?? [];
  const serviceRoles = rolesQ.data ?? [];

  // Оптимистичный слой: ключ `entity::role::action` → true (allow) | false
  // (revoked). Перетирает то, что пришло с сервера, до следующего refetch'а.
  const [optimistic, setOptimistic] = useState<Record<string, boolean>>({});
  // Какие ячейки сейчас в полёте — чтобы рисовать спиннер и блокировать клик.
  const [saving, setSaving] = useState<Record<string, CellStatus>>({});
  // Кастомные роли, созданные в этой сессии: показываем строкой сразу, ещё до
  // того как появится первый grant (без грантов backend их в матрице не вернёт).
  const [localRoles, setLocalRoles] = useState<RoleName[]>([]);

  // Индекс серверных grant'ов по (entity, role, action).
  const serverIndex = useMemo(() => {
    const m = new Map<string, PermissionEntry>();
    for (const g of grants) {
      m.set(`${g.entity_type}::${g.role}::${g.action}`, g);
    }
    return m;
  }, [grants]);

  // Эффективное состояние ячейки: оптимистичный слой важнее серверного.
  const isAllowed = useCallback(
    (entity: EntityType, role: RoleName, action: ActionName): boolean => {
      const key = `${entity}::${role}::${action}`;
      if (key in optimistic) return optimistic[key];
      return serverIndex.has(key);
    },
    [optimistic, serverIndex],
  );

  // Все кастомные роли отдела: из существующих grant'ов + созданные локально.
  const customRoles = useMemo(() => {
    const set = new Set<RoleName>();
    for (const g of grants) {
      if (!SYSTEM_ROLES.includes(g.role)) set.add(g.role);
    }
    for (const r of localRoles) set.add(r);
    return set;
  }, [grants, localRoles]);

  // Роли таблицы для конкретной сущности: системные всегда + все кастомные
  // отдела (даже без grant'ов на эту сущность — пустую строчку видно).
  const rolesFor = useCallback(
    (_entity: EntityType): RoleName[] => {
      const set = new Set<RoleName>(SYSTEM_ROLE_ORDER);
      for (const r of customRoles) set.add(r);
      return Array.from(set).sort((a, b) =>
        roleSortKey(a).localeCompare(roleSortKey(b)),
      );
    },
    [customRoles],
  );

  const toggleCell = useCallback(
    async (
      entity: EntityType,
      role: RoleName,
      action: PermissionCatalogAction,
    ) => {
      if (role in LOCKED_ROLES) return;
      if (action.worker_only) return;
      const key = `${entity}::${role}::${action.action}`;
      if (saving[key] === "saving") return;

      const currentlyAllowed = isAllowed(entity, role, action.action);
      const next = !currentlyAllowed;

      // Оптимистично применяем + помечаем «в полёте».
      setOptimistic((m) => ({ ...m, [key]: next }));
      setSaving((m) => ({ ...m, [key]: "saving" }));

      try {
        if (next) {
          await setPermission(entity, role, action.action as ActionName, {
            target_department_id: myDept || undefined,
          });
          if (action.sensitive) {
            toast.warn(
              `${entity}/${role}/${action.action} выдано · sensitive → CRITICAL audit`,
            );
          } else {
            toast.success(`Выдано: ${entity}/${role}/${action.action}`);
          }
        } else {
          await deletePermission(entity, role, action.action as ActionName, {
            target_department_id: myDept || undefined,
          });
          toast.success(`Отозвано: ${entity}/${role}/${action.action}`);
        }
        // Успех — чистим оптимистичный слой по ключу и подтягиваем правду.
        setOptimistic((m) => {
          const { [key]: _drop, ...rest } = m;
          return rest;
        });
        bump();
      } catch (e) {
        // Откат: возвращаем ячейку к серверному состоянию.
        setOptimistic((m) => {
          const { [key]: _drop, ...rest } = m;
          return rest;
        });
        toast.error(apiErrMsg(e));
      } finally {
        setSaving((m) => {
          const { [key]: _drop, ...rest } = m;
          return rest;
        });
      }
    },
    [bump, isAllowed, myDept, saving, toast],
  );

  // Скопировать все grant'ы базовой роли в новую (best-effort, по каталогу).
  const copyGrantsFromBase = useCallback(
    async (base: RoleName, target: RoleName) => {
      for (const entity of catalog) {
        for (const a of entity.actions) {
          if (a.worker_only) continue;
          if (!isAllowed(entity.entity_type, base, a.action)) continue;
          try {
            await setPermission(
              entity.entity_type,
              target,
              a.action as ActionName,
              { target_department_id: myDept || undefined },
            );
          } catch {
            // Частичный сбой копирования не должен валить создание роли —
            // расхождение видно в матрице, пользователь доставит вручную.
          }
        }
      }
    },
    [catalog, isAllowed, myDept],
  );

  const onRoleCreated = useCallback(
    async (roleName: RoleName, base: RoleName | null) => {
      setLocalRoles((rs) => (rs.includes(roleName) ? rs : [...rs, roleName]));
      if (base) {
        await copyGrantsFromBase(base, roleName);
      }
      bump();
    },
    [bump, copyGrantsFromBase],
  );

  // Имена ролей, уже занятые (системные + существующие/локальные кастомные).
  const existingRoleNames = useMemo(() => {
    const set = new Set<string>(SYSTEM_ROLES);
    for (const r of customRoles) set.add(r);
    return set;
  }, [customRoles]);

  // Все роли для выбора «на основе» в форме создания.
  const baseRoleChoices = useMemo(
    () => rolesFor("server" as EntityType),
    [rolesFor],
  );

  if (catalogQ.loading || grantsQ.loading) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="spinner">Загрузка матрицы…</div>
      </div>
    );
  }

  if (catalogQ.error || grantsQ.error) {
    const err = catalogQ.error ?? grantsQ.error;
    return (
      <div className="flex-1 p-8">
        <div className="alert-danger flex items-center gap-2">
          <span>{apiErrMsg(err)}</span>
          <button
            className="btn btn-sm"
            onClick={() => {
              catalogQ.refetch();
              grantsQ.refetch();
            }}
          >
            Повторить
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 flex flex-col gap-4">
      <Tabs
        className="border-b border-token flex gap-1 shrink-0"
        active={mode}
        onChange={(id) => setMode(id as ViewMode)}
        tabs={[
          {
            id: "matrix",
            label: "Матрица",
            icon: <Grid3x3 className="w-4 h-4" />,
          },
          {
            id: "role",
            label: "Управление ролью",
            icon: <SlidersHorizontal className="w-4 h-4" />,
          },
        ]}
      />

      {mode === "matrix" ? (
        <>
          <div className="card">
            <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
              <h3 className="font-semibold flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-accent" />
                Матрица разрешений · server_service
              </h3>
              <span className="text-xs text-dim">
                {grants.length} grant-строк · scope:{" "}
                <span className="mono">{myDept ?? "—"}</span>
              </span>
            </div>
            <p className="text-xs text-dim leading-relaxed">
              Клик по чекбоксу мгновенно выдаёт или отзывает действие для роли.
              Строки <span className="mono">admin</span> и{" "}
              <span className="mono">guest</span> залочены. Изменения пишутся в
              рамках вашего отдела (
              <span className="mono">{myDept ?? "—"}</span>).
            </p>
            <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim flex items-center gap-4 flex-wrap">
              <span className="flex items-center gap-1">
                <Lock className="w-3 h-3" /> роль залочена
              </span>
              <span className="flex items-center gap-1">
                <span className="badge badge-warn text-[10px]">!</span>{" "}
                sensitive → CRITICAL audit
              </span>
              <span className="flex items-center gap-1">
                <Bot className="w-3 h-3" /> worker_only — людям не выдаётся
              </span>
            </div>

            <NewRoleForm
              departmentId={myDept}
              existingNames={existingRoleNames}
              baseRoles={baseRoleChoices}
              onCreated={onRoleCreated}
            />
          </div>

          {catalog.length === 0 ? (
            <div className="empty-card text-sm text-dim">
              Каталог пуст — backend не вернул ни одной сущности.
            </div>
          ) : (
            catalog.map((entity) => (
              <EntityMatrix
                key={entity.entity_type}
                entity={entity}
                roles={rolesFor(entity.entity_type)}
                isAllowed={isAllowed}
                saving={saving}
                onToggle={toggleCell}
              />
            ))
          )}
        </>
      ) : (
        <RoleEditor
          departmentId={myDept}
          catalog={catalog}
          serviceRoles={serviceRoles}
          rolesLoading={rolesQ.loading}
          rolesError={rolesQ.error}
          onRolesRefetch={() => rolesQ.refetch()}
          isAllowed={isAllowed}
          saving={saving}
          onToggle={toggleCell}
          onRolesChanged={bump}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Role editor — точечное управление одной ролью
// ---------------------------------------------------------------------------

type IsAllowedFn = (
  entity: EntityType,
  role: RoleName,
  action: ActionName,
) => boolean;

type ToggleFn = (
  entity: EntityType,
  role: RoleName,
  action: PermissionCatalogAction,
) => void;

function RoleEditor({
  departmentId,
  catalog,
  serviceRoles,
  rolesLoading,
  rolesError,
  onRolesRefetch,
  isAllowed,
  saving,
  onToggle,
  onRolesChanged,
}: {
  departmentId: string | null;
  catalog: PermissionCatalogItem[];
  serviceRoles: ServiceRole[];
  rolesLoading: boolean;
  rolesError: Error | null;
  onRolesRefetch: () => void;
  isAllowed: IsAllowedFn;
  saving: Record<string, CellStatus>;
  onToggle: ToggleFn;
  onRolesChanged: () => void;
}) {
  const [selected, setSelected] = useState<string>("");

  // Системные роли каталога не приходят в service_roles (он хранит только
  // кастомные определения отдела), поэтому добиваем их вручную — точечно
  // править права системных reader/operator тоже нужно.
  const allRoleNames = useMemo(() => {
    const set = new Set<string>(SYSTEM_ROLE_ORDER);
    for (const r of serviceRoles) set.add(r.role_name);
    return Array.from(set).sort((a, b) =>
      roleSortKey(a).localeCompare(roleSortKey(b)),
    );
  }, [serviceRoles]);

  // Определение выбранной роли из auth-каталога (есть только у кастомных —
  // системные тут отсутствуют, тогда роль считаем системной без description).
  const selectedDef = useMemo(
    () => serviceRoles.find((r) => r.role_name === selected) ?? null,
    [serviceRoles, selected],
  );

  const isSystem =
    SYSTEM_ROLES.includes(selected as RoleName) ||
    (selectedDef?.is_system ?? false);
  const isLocked = selected in LOCKED_ROLES;

  if (rolesLoading) {
    return (
      <div className="card flex items-center justify-center py-8">
        <div className="spinner">Загрузка ролей…</div>
      </div>
    );
  }

  if (rolesError) {
    return (
      <div className="card">
        <div className="alert-danger flex items-center gap-2">
          <span>{apiErrMsg(rolesError)}</span>
          <button className="btn btn-sm" onClick={onRolesRefetch}>
            Повторить
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="font-semibold flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-accent" />
            Управление ролью · server_service
          </h3>
          <span className="text-xs text-dim">
            scope: <span className="mono">{departmentId ?? "—"}</span>
          </span>
        </div>
        <label className="flex flex-col gap-1 text-xs max-w-sm">
          <span className="text-dim">роль отдела</span>
          <select
            className="input"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">— выберите роль —</option>
            {allRoleNames.map((r) => (
              <option key={r} value={r}>
                {r}
                {SYSTEM_ROLES.includes(r as RoleName) ? " · system" : ""}
              </option>
            ))}
          </select>
        </label>
        <p className="text-[11px] text-dim mt-2 leading-relaxed">
          Выберите роль, чтобы видеть и редактировать её права по сущностям.
          Системные <span className="mono">admin/guest</span> залочены,
          удалять можно только кастомные роли.
        </p>
      </div>

      {!selected ? (
        <div className="empty-card text-sm text-dim">
          Роль не выбрана.
        </div>
      ) : (
        <>
          <RoleMeta
            departmentId={departmentId}
            roleName={selected}
            def={selectedDef}
            isSystem={isSystem}
            onChanged={() => {
              onRolesRefetch();
              onRolesChanged();
            }}
            onDeleted={() => {
              setSelected("");
              onRolesRefetch();
              onRolesChanged();
            }}
          />

          {isLocked && (
            <div className="alert-warn flex items-center gap-2 text-sm">
              <Lock className="w-4 h-4" />
              <span>
                Роль <span className="mono">{selected}</span> залочена —{" "}
                {LOCKED_ROLES[selected]}. Права не редактируются.
              </span>
            </div>
          )}

          {catalog.length === 0 ? (
            <div className="empty-card text-sm text-dim">
              Каталог пуст — backend не вернул ни одной сущности.
            </div>
          ) : (
            catalog.map((entity) => (
              <RoleEntityCard
                key={entity.entity_type}
                entity={entity}
                role={selected}
                locked={isLocked}
                isAllowed={isAllowed}
                saving={saving}
                onToggle={onToggle}
              />
            ))
          )}
        </>
      )}
    </div>
  );
}

// Метаданные роли: description (PATCH) + удаление (DELETE). Имя read-only —
// backend rename не поддерживает.
function RoleMeta({
  departmentId,
  roleName,
  def,
  isSystem,
  onChanged,
  onDeleted,
}: {
  departmentId: string | null;
  roleName: string;
  def: ServiceRole | null;
  isSystem: boolean;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(def?.description ?? "");
  const [busy, setBusy] = useState(false);

  // description редактируется только у кастомных ролей, у которых есть
  // определение в auth-каталоге. Системные admin/guest и роли без def — нет.
  const canEditDesc = !isSystem && def !== null && departmentId !== null;
  const canDelete = !isSystem && def !== null && departmentId !== null;

  function startEdit() {
    setDraft(def?.description ?? "");
    setEditing(true);
  }

  async function saveDesc() {
    if (!departmentId) return;
    setBusy(true);
    try {
      await patchServiceRole(departmentId, "server_service", roleName, {
        description: draft.trim(),
      });
      toast.success(`Описание роли ${roleName} обновлено`);
      setEditing(false);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!departmentId) return;
    if (
      !(await confirm.confirm({
        message: `Удалить роль ${roleName}? Снести её определение можно только без активных назначений.`,
        danger: true,
        confirmLabel: "Удалить",
      }))
    )
      return;
    setBusy(true);
    try {
      await deleteServiceRole(departmentId, "server_service", roleName);
      toast.success(`Роль ${roleName} удалена`);
      onDeleted();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-semibold mono text-sm">
            <ShieldCheck className="w-4 h-4 text-accent" />
            {roleName}
            {isSystem && (
              <span className="badge text-[10px] flex items-center gap-1">
                <Lock className="w-3 h-3" /> system
              </span>
            )}
          </div>
          {!editing && (
            <div className="text-xs text-dim mt-1">
              {def?.description || (
                <span className="italic opacity-70">без описания</span>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {canEditDesc && !editing && (
            <button
              className="btn btn-sm btn-ghost flex items-center gap-1"
              onClick={startEdit}
              disabled={busy}
            >
              <Pencil className="w-3 h-3" /> описание
            </button>
          )}
          {canDelete && (
            <button
              className="btn btn-sm btn-danger flex items-center gap-1"
              onClick={onDelete}
              disabled={busy}
            >
              {busy ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Trash2 className="w-3 h-3" />
              )}
              удалить
            </button>
          )}
        </div>
      </div>

      {editing && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs flex-1 min-w-[200px]">
            <span className="text-dim">описание</span>
            <input
              className="input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={busy}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") saveDesc();
                if (e.key === "Escape") setEditing(false);
              }}
            />
          </label>
          <button
            className="btn btn-sm btn-primary flex items-center gap-1"
            onClick={saveDesc}
            disabled={busy}
          >
            {busy ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Check className="w-3 h-3" />
            )}
            сохранить
          </button>
          <button
            className="btn btn-sm btn-ghost flex items-center gap-1"
            onClick={() => setEditing(false)}
            disabled={busy}
          >
            <X className="w-3 h-3" /> отмена
          </button>
        </div>
      )}

      {isSystem && (
        <p className="text-[10px] text-dim mt-2 leading-relaxed">
          Системную роль нельзя удалить или переименовать. Backend не
          поддерживает rename ни для каких ролей — меняется только описание
          кастомных.
        </p>
      )}
    </div>
  );
}

// Карточка сущности в режиме роли: список действий с чекбоксом для одной роли.
function RoleEntityCard({
  entity,
  role,
  locked,
  isAllowed,
  saving,
  onToggle,
}: {
  entity: PermissionCatalogItem;
  role: RoleName;
  locked: boolean;
  isAllowed: IsAllowedFn;
  saving: Record<string, CellStatus>;
  onToggle: ToggleFn;
}) {
  return (
    <div className="card">
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h4 className="font-semibold mono text-sm">{entity.entity_type}</h4>
        <span className="text-xs text-dim">{entity.description}</span>
      </div>

      {entity.actions.length === 0 ? (
        <div className="text-sm text-dim">
          У сущности нет действий в каталоге.
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-token border border-token rounded overflow-auto max-h-[60vh]">
          {entity.actions.map((a) => {
            const key = `${entity.entity_type}::${role}::${a.action}`;
            const allowed = isAllowed(entity.entity_type, role, a.action);
            const isSaving = saving[key] === "saving";
            const disabled = locked || a.worker_only;
            const reason = locked
              ? "роль залочена"
              : a.worker_only
                ? "worker_only — выдавать людям нельзя"
                : allowed
                  ? "Снять — отозвать"
                  : "Поставить — выдать";
            return (
              <label
                key={a.action}
                className={[
                  "flex items-center gap-3 px-3 py-2",
                  disabled
                    ? "opacity-60"
                    : "cursor-pointer hover:bg-[var(--bg-soft)]",
                ].join(" ")}
                title={`${a.description}\n\n${reason}`}
              >
                <PermCheckbox
                  allowed={allowed}
                  disabled={disabled}
                  saving={isSaving}
                  title={`${a.description}\n\n${reason}`}
                  onClick={() => onToggle(entity.entity_type, role, a)}
                />
                <span className="mono text-xs flex items-center gap-1">
                  {a.action}
                  {a.sensitive && (
                    <span
                      className="badge badge-warn text-[10px]"
                      title="sensitive → CRITICAL audit"
                    >
                      !
                    </span>
                  )}
                  {a.worker_only && (
                    <Bot className="w-3 h-3" aria-label="worker_only" />
                  )}
                </span>
                <span className="text-[11px] text-dim flex-1 truncate">
                  {a.description}
                </span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// New custom role
// ---------------------------------------------------------------------------

function NewRoleForm({
  departmentId,
  existingNames,
  baseRoles,
  onCreated,
}: {
  departmentId: string | null;
  existingNames: Set<string>;
  baseRoles: RoleName[];
  onCreated: (roleName: RoleName, base: RoleName | null) => void | Promise<void>;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [base, setBase] = useState("");
  const [busy, setBusy] = useState(false);

  function reset() {
    setName("");
    setBase("");
    setOpen(false);
  }

  async function submit() {
    const roleName = name.trim();
    if (!roleName) {
      toast.warn("Имя роли обязательно");
      return;
    }
    if (existingNames.has(roleName)) {
      toast.error(`Роль ${roleName} уже существует`);
      return;
    }
    if (!departmentId) {
      toast.error("Не определён отдел — создать роль нельзя");
      return;
    }
    setBusy(true);
    try {
      await createServiceRole(departmentId, "server_service", {
        role_name: roleName,
        description: base ? `на основе ${base}` : undefined,
      });
      toast.success(`Роль ${roleName} создана`);
      await onCreated(roleName, base || null);
      reset();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-3 pt-3 border-t border-token">
        <button
          className="btn btn-ghost text-xs flex items-center gap-1"
          onClick={() => setOpen(true)}
        >
          <Plus className="w-3 h-3" /> Новая роль
        </button>
      </div>
    );
  }

  return (
    <div className="mt-3 pt-3 border-t border-token">
      <div className="p-3 border border-token rounded">
        <div className="text-sm font-semibold mb-2 flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" />
            Новая роль · server_service
          </span>
          <button
            className="btn btn-ghost text-xs flex items-center gap-1"
            onClick={reset}
            disabled={busy}
          >
            <X className="w-3 h-3" /> закрыть
          </button>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-dim">имя роли</span>
            <input
              className="input mono"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="server_restarter"
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-dim">на основе (опционально)</span>
            <select
              className="input"
              value={base}
              onChange={(e) => setBase(e.target.value)}
              disabled={busy}
            >
              <option value="">— с нуля —</option>
              {baseRoles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={submit}
            disabled={busy || !name.trim()}
          >
            {busy && <Loader2 className="w-3 h-3 animate-spin" />}
            Создать
          </button>
        </div>
        <p className="text-[10px] text-dim mt-2 leading-relaxed">
          Роль создаётся в каталоге <span className="mono">(отдел, server_service)</span>.
          С базовой ролью её grant'ы копируются в новую — дальше правьте
          чекбоксами. Системные роли{" "}
          (<span className="mono">guest/reader/operator/admin</span>)
          дублировать нельзя.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-entity matrix
// ---------------------------------------------------------------------------

function EntityMatrix({
  entity,
  roles,
  isAllowed,
  saving,
  onToggle,
}: {
  entity: PermissionCatalogItem;
  roles: RoleName[];
  isAllowed: (
    entity: EntityType,
    role: RoleName,
    action: ActionName,
  ) => boolean;
  saving: Record<string, CellStatus>;
  onToggle: (
    entity: EntityType,
    role: RoleName,
    action: PermissionCatalogAction,
  ) => void;
}) {
  return (
    <div className="card">
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h4 className="font-semibold mono text-sm flex items-center gap-2">
          {entity.entity_type}
        </h4>
        <span className="text-xs text-dim">{entity.description}</span>
      </div>

      {entity.actions.length === 0 ? (
        <div className="text-sm text-dim">
          У сущности нет действий в каталоге.
        </div>
      ) : (
        <div className="overflow-auto max-h-[60vh] border border-token rounded">
          <table className="text-sm border-collapse">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pt-2 px-3 sticky left-0 top-0 z-20 bg-[var(--bg-soft)]">
                  role
                </th>
                {entity.actions.map((a) => (
                  <th
                    key={a.action}
                    className="pb-2 pt-2 px-2 mono font-normal align-bottom sticky top-0 z-10 bg-[var(--bg-soft)]"
                    title={a.description}
                  >
                    <div className="flex items-center gap-1 whitespace-nowrap">
                      <span>{a.action}</span>
                      {a.sensitive && (
                        <span
                          className="badge badge-warn text-[10px]"
                          title="sensitive → CRITICAL audit"
                        >
                          !
                        </span>
                      )}
                      {a.worker_only && (
                        <Bot
                          className="w-3 h-3"
                          aria-label="worker_only"
                        />
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => {
                const locked = role in LOCKED_ROLES;
                return (
                  <tr key={role} className="border-t border-token">
                    <td className="py-2 px-3 mono text-xs sticky left-0 z-10 bg-[var(--bg-soft)]">
                      <span
                        className="flex items-center gap-1"
                        title={locked ? LOCKED_ROLES[role] : undefined}
                      >
                        {locked && <Lock className="w-3 h-3 text-dim" />}
                        {role}
                      </span>
                    </td>
                    {entity.actions.map((a) => {
                      const key = `${entity.entity_type}::${role}::${a.action}`;
                      const allowed = isAllowed(
                        entity.entity_type,
                        role,
                        a.action,
                      );
                      const isSaving = saving[key] === "saving";
                      const disabled = locked || a.worker_only;
                      const reason = locked
                        ? LOCKED_ROLES[role]
                        : a.worker_only
                          ? "worker_only — выдавать людям нельзя"
                          : allowed
                            ? "Снять — отозвать"
                            : "Поставить — выдать";
                      return (
                        <td key={a.action} className="py-1.5 px-2 text-center">
                          <PermCheckbox
                            allowed={allowed}
                            disabled={disabled}
                            saving={isSaving}
                            title={`${a.description}\n\n${reason}`}
                            onClick={() =>
                              onToggle(entity.entity_type, role, a)
                            }
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Checkbox cell
// ---------------------------------------------------------------------------

function PermCheckbox({
  allowed,
  disabled,
  saving,
  title,
  onClick,
}: {
  allowed: boolean;
  disabled: boolean;
  saving: boolean;
  title: string;
  onClick: () => void;
}) {
  if (saving) {
    return (
      <span
        className="inline-flex items-center justify-center w-4 h-4 align-middle"
        title={title}
      >
        <Loader2 className="w-3.5 h-3.5 animate-spin text-dim" />
      </span>
    );
  }
  return (
    <input
      type="checkbox"
      className={[
        "w-4 h-4 align-middle accent-[var(--accent)]",
        disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
      ].join(" ")}
      checked={allowed}
      disabled={disabled}
      title={title}
      onChange={onClick}
    />
  );
}
