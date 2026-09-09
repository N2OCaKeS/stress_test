import { useCallback, useMemo, useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  Lock,
  Loader2,
  Plus,
  X,
  Grid3x3,
  SlidersHorizontal,
  Trash2,
  Check,
  Pencil,
  RotateCcw,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Tabs } from "@/components/ui/Tabs";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import { useQuery } from "@/api/auth/useQuery";
import {
  getSecretPermissionCatalog,
  listSecretPermissions,
  setSecretPermission,
  deleteSecretPermission,
} from "@/api/secret/permissions";
import {
  createServiceRole,
  listServiceRoles,
  patchServiceRole,
  deleteServiceRole,
} from "@/api/auth/service_roles";
import { apiErrMsg } from "@/api/client";
import { isSecretZoneBlocked, personaDeptId } from "@/lib/rbac";
import type { ServiceRole } from "@/api/auth/types";
import type {
  SecretActionName,
  SecretPermissionCatalogAction,
  SecretPermissionCatalogItem,
  SecretPermissionEntry,
  SecretRoleName,
} from "@/api/secret/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";

/**
 * Тип-wide матрица разрешений `secret_service`. У сервиса ровно одна
 * управляемая сущность — `secret`, поэтому таблица одна: роли × действия,
 * каждая ячейка — чекбокс, клик по которому копит правку локально; батч
 * применяется кнопкой «Сохранить» (PUT для allow, DELETE для revoke).
 *
 * Строки `admin` и `guest` залочены фиксированной матрицей секретов (backend
 * отбивает grant/revoke по ним 409 SYSTEM_ROLE_IMMUTABLE). Редактируются
 * только кастомные роли отдела; завести их можно кнопкой «Новая роль».
 *
 * Точечный доступ к конкретной кред'е (RoleACL / DeptGrant / UserACL) — это
 * отдельная страница «Доступ к секретам»; здесь только тип-wide правила.
 *
 * RBAC: secret_service dept-scoped. Платформенные роли без отдела backend
 * режет 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT — им страница не видна.
 * Видна department_admin своего отдела и носителю service-роли secret.admin.
 */
export function ServicesSecretPermissions() {
  const { persona } = usePersona();
  const blocked = isSecretZoneBlocked(persona);
  const canView =
    persona.platform_role === "dep_admin" ||
    persona.service_roles?.secret === "admin";
  if (blocked || !canView) {
    return (
      <div className="p-8">
        <div className="alert-danger flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          <span>
            403 · матрица разрешений `secret_service` доступна
            department_admin своего отдела или роли
            <span className="mono"> secret.admin</span>.
          </span>
        </div>
      </div>
    );
  }
  return <ServicesSecretPermissionsLive />;
}

// Каталог ролей auth-сервиса для secret_service заведён под именем `secret`
// (см. ServicesSecretAccess) — им и оперируем при create/patch/delete/list.
const SECRET_SERVICE = "secret";

// Роли, чьи правила нельзя менять из этого UI.
const LOCKED_ROLES: Record<string, string> = {
  admin: "admin держит полный доступ — правила не редактируются",
  guest: "guest — базовая роль, правила не редактируются",
};

// Системные роли каталога — их нельзя дублировать кастомной.
const SYSTEM_ROLES: SecretRoleName[] = ["guest", "admin"];

// Порядок системных ролей в таблице; кастомные идут после по алфавиту.
const SYSTEM_ROLE_ORDER: SecretRoleName[] = ["guest", "admin"];

function roleSortKey(role: SecretRoleName): string {
  const idx = SYSTEM_ROLE_ORDER.indexOf(role);
  return idx >= 0 ? `0${idx}` : `1${role}`;
}

type ViewMode = "matrix" | "role";

function ServicesSecretPermissionsLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const myDept = personaDeptId(persona);

  const [mode, setMode] = useState<ViewMode>("matrix");
  const [refreshTick, setRefreshTick] = useState(0);
  const bump = useCallback(() => setRefreshTick((t) => t + 1), []);

  const catalogQ = useQuery(() => getSecretPermissionCatalog(), []);
  const grantsQ = useQuery(
    () => listSecretPermissions({ describe: true }),
    [refreshTick],
  );
  // Каталог ролей secret_service отдела — для режима «Управление ролью».
  const rolesQ = useQuery(
    () =>
      myDept
        ? listServiceRoles(myDept, SECRET_SERVICE)
        : Promise.resolve([] as ServiceRole[]),
    [myDept, refreshTick],
  );

  const catalog = useMemo(() => catalogQ.data ?? [], [catalogQ.data]);
  const grants = useMemo(() => grantsQ.data?.items ?? [], [grantsQ.data]);
  const serviceRoles = rolesQ.data ?? [];

  // Локальные правки: ключ `role::action` → желаемое allow (true) /
  // revoked (false). Копятся до батч-сохранения.
  const [edits, setEdits] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  // Кастомные роли, созданные в этой сессии: показываем строкой сразу, ещё до
  // появления первого grant (без грантов backend их в матрице не вернёт).
  const [localRoles, setLocalRoles] = useState<SecretRoleName[]>([]);

  // Индекс grant'ов по (role, action).
  const serverIndex = useMemo(() => {
    const m = new Map<string, SecretPermissionEntry>();
    for (const g of grants) m.set(`${g.role}::${g.action}`, g);
    return m;
  }, [grants]);

  const serverAllowed = useCallback(
    (key: string): boolean => serverIndex.has(key),
    [serverIndex],
  );

  // Эффективное состояние ячейки: локальная правка важнее серверной.
  const isAllowed = useCallback(
    (role: SecretRoleName, action: SecretActionName): boolean => {
      const key = `${role}::${action}`;
      if (key in edits) return edits[key];
      return serverAllowed(key);
    },
    [edits, serverAllowed],
  );

  // Все кастомные роли отдела: из существующих grant'ов + созданные локально.
  const customRoles = useMemo(() => {
    const set = new Set<SecretRoleName>();
    for (const g of grants) {
      if (!SYSTEM_ROLES.includes(g.role)) set.add(g.role);
    }
    for (const r of localRoles) set.add(r);
    return set;
  }, [grants, localRoles]);

  // Роли таблицы: системные всегда + все кастомные отдела.
  const roles = useMemo((): SecretRoleName[] => {
    const set = new Set<SecretRoleName>(SYSTEM_ROLE_ORDER);
    for (const r of customRoles) set.add(r);
    return Array.from(set).sort((a, b) =>
      roleSortKey(a).localeCompare(roleSortKey(b)),
    );
  }, [customRoles]);

  // Клик по ячейке только копит правку — без сети.
  const toggleCell = useCallback(
    (role: SecretRoleName, action: SecretPermissionCatalogAction) => {
      if (role in LOCKED_ROLES) return;
      const key = `${role}::${action.action}`;
      setEdits((m) => ({ ...m, [key]: !isAllowed(role, action.action) }));
    },
    [isAllowed],
  );

  // Снять все права роли в таблице — стейдж revoke.
  const clearRole = useCallback(
    (entity: SecretPermissionCatalogItem, role: SecretRoleName) => {
      if (role in LOCKED_ROLES) return;
      setEdits((m) => {
        const next = { ...m };
        for (const a of entity.actions) next[`${role}::${a.action}`] = false;
        return next;
      });
    },
    [],
  );

  // Снять права всех незалоченных ролей — стейдж revoke.
  const clearAllRoles = useCallback(
    (entity: SecretPermissionCatalogItem) => {
      setEdits((m) => {
        const next = { ...m };
        for (const role of roles) {
          if (role in LOCKED_ROLES) continue;
          for (const a of entity.actions) next[`${role}::${a.action}`] = false;
        }
        return next;
      });
    },
    [roles],
  );

  // Ключи диффа (правка != сервер) среди подмножества.
  const diffKeys = useCallback(
    (predicate: (key: string) => boolean): string[] =>
      Object.keys(edits).filter(
        (k) => predicate(k) && edits[k] !== serverAllowed(k),
      ),
    [edits, serverAllowed],
  );

  const roleDiffKeys = useCallback(
    (role: SecretRoleName) => diffKeys((k) => k.split("::")[0] === role),
    [diffKeys],
  );

  const allDiffKeys = useCallback(() => diffKeys(() => true), [diffKeys]);

  // Применить дифф: PUT для allow, DELETE для revoke.
  const applyDiff = useCallback(
    async (keys: string[]) => {
      if (saving || keys.length === 0) return;
      setSaving(true);
      const done: string[] = [];
      let ok = 0;
      let failed = 0;
      for (const key of keys) {
        const [role, action] = key.split("::") as [
          SecretRoleName,
          SecretActionName,
        ];
        try {
          if (edits[key]) {
            await setSecretPermission(role, action, {
              target_department_id: myDept || undefined,
            });
          } else {
            await deleteSecretPermission(role, action, {
              target_department_id: myDept || undefined,
            });
          }
          done.push(key);
          ok++;
        } catch (e) {
          failed++;
          toast.error(apiErrMsg(e));
        }
      }
      setEdits((m) => {
        const next = { ...m };
        for (const k of done) delete next[k];
        return next;
      });
      setSaving(false);
      if (ok) toast.success(`Сохранено изменений: ${ok}`);
      if (failed) toast.warn(`Не сохранено: ${failed}`);
      bump();
    },
    [saving, edits, myDept, toast, bump],
  );

  // Снять несохранённые правки в подмножестве (Отмена).
  const cancelKeys = useCallback((keys: string[]) => {
    setEdits((m) => {
      const next = { ...m };
      for (const k of keys) delete next[k];
      return next;
    });
  }, []);

  const allKeysIn = useCallback(
    (predicate: (key: string) => boolean): string[] =>
      Object.keys(edits).filter(predicate),
    [edits],
  );

  // Скопировать grant'ы базовой роли в новую (best-effort, по каталогу).
  const copyGrantsFromBase = useCallback(
    async (base: SecretRoleName, target: SecretRoleName) => {
      for (const entity of catalog) {
        for (const a of entity.actions) {
          if (!isAllowed(base, a.action)) continue;
          try {
            await setSecretPermission(target, a.action, {
              target_department_id: myDept || undefined,
            });
          } catch {
            // Частичный сбой копирования не валит создание роли — расхождение
            // видно в матрице, пользователь доставит вручную.
          }
        }
      }
    },
    [catalog, isAllowed, myDept],
  );

  const onRoleCreated = useCallback(
    async (roleName: SecretRoleName, base: SecretRoleName | null) => {
      setLocalRoles((rs) => (rs.includes(roleName) ? rs : [...rs, roleName]));
      if (base) await copyGrantsFromBase(base, roleName);
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
          <Button size="sm"
            onClick={() => {
              catalogQ.refetch();
              grantsQ.refetch();
            }}
          >
            Повторить
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-auto p-6 flex flex-col gap-4">
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
                Глобальные (тип-wide) правила · secret_service
              </h3>
              <span className="text-xs text-dim">
                {grants.length} grant-строк · scope:{" "}
                <span className="mono">{myDept ?? "—"}</span>
              </span>
            </div>
            <p className="text-xs text-dim leading-relaxed">
              Правила на весь <em>тип</em> сущности (все секреты отдела). Клик по
              чекбоксу копит правку локально; применяется батчем кнопкой
              «Сохранить». Строки <span className="mono">admin</span> и{" "}
              <span className="mono">guest</span> залочены. Изменения пишутся в
              рамках вашего отдела (<span className="mono">{myDept ?? "—"}</span>
              ).
            </p>
            <p className="text-[11px] text-dim leading-relaxed mt-2">
              Точечный доступ к <em>конкретной</em> кред'е (RoleACL / DeptGrant /
              UserACL) — на странице «Доступ к секретам».
            </p>
            <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim flex items-center gap-4 flex-wrap">
              <span className="flex items-center gap-1">
                <Lock className="w-3 h-3" /> роль залочена
              </span>
            </div>

            <NewRoleForm
              departmentId={myDept}
              existingNames={existingRoleNames}
              baseRoles={roles}
              onCreated={onRoleCreated}
            />
          </div>

          {catalog.length === 0 ? (
            <div className="empty-card text-sm text-dim">
              Каталог пуст — backend не вернул ни одной сущности.
            </div>
          ) : (
            catalog.map((entity) => {
              const dkeys = allDiffKeys();
              return (
                <EntityMatrix
                  key={entity.entity_type}
                  entity={entity}
                  roles={roles}
                  isAllowed={isAllowed}
                  saving={saving}
                  onToggle={toggleCell}
                  dirtyCount={dkeys.length}
                  onSave={() => applyDiff(dkeys)}
                  onCancel={() => cancelKeys(allKeysIn(() => true))}
                  onClearRole={(role) => clearRole(entity, role)}
                  onClearAllRoles={() => clearAllRoles(entity)}
                  hasOverride={(role) =>
                    entity.actions.some((a) => isAllowed(role, a.action))
                  }
                />
              );
            })
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
          roleDirtyCount={(role) => roleDiffKeys(role).length}
          onSaveRole={(role) => applyDiff(roleDiffKeys(role))}
          onCancelRole={(role) =>
            cancelKeys(allKeysIn((k) => k.split("::")[0] === role))
          }
          onClearRoleEverywhere={(role) => {
            if (role in LOCKED_ROLES) return;
            setEdits((m) => {
              const next = { ...m };
              for (const entity of catalog) {
                for (const a of entity.actions) {
                  next[`${role}::${a.action}`] = false;
                }
              }
              return next;
            });
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Role editor — точечное управление одной ролью
// ---------------------------------------------------------------------------

type IsAllowedFn = (role: SecretRoleName, action: SecretActionName) => boolean;

type ToggleFn = (
  role: SecretRoleName,
  action: SecretPermissionCatalogAction,
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
  roleDirtyCount,
  onSaveRole,
  onCancelRole,
  onClearRoleEverywhere,
}: {
  departmentId: string | null;
  catalog: SecretPermissionCatalogItem[];
  serviceRoles: ServiceRole[];
  rolesLoading: boolean;
  rolesError: Error | null;
  onRolesRefetch: () => void;
  isAllowed: IsAllowedFn;
  saving: boolean;
  onToggle: ToggleFn;
  onRolesChanged: () => void;
  roleDirtyCount: (role: SecretRoleName) => number;
  onSaveRole: (role: SecretRoleName) => void;
  onCancelRole: (role: SecretRoleName) => void;
  onClearRoleEverywhere: (role: SecretRoleName) => void;
}) {
  const [selected, setSelected] = useState<string>("");

  // Системные guest/admin не приходят в service_roles (он хранит только
  // кастомные определения отдела), поэтому добиваем их вручную.
  const allRoleNames = useMemo(() => {
    const set = new Set<string>(SYSTEM_ROLE_ORDER);
    for (const r of serviceRoles) set.add(r.role_name);
    return Array.from(set).sort((a, b) =>
      roleSortKey(a).localeCompare(roleSortKey(b)),
    );
  }, [serviceRoles]);

  const selectedDef = useMemo(
    () => serviceRoles.find((r) => r.role_name === selected) ?? null,
    [serviceRoles, selected],
  );

  const isSystem =
    SYSTEM_ROLES.includes(selected as SecretRoleName) ||
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
          <Button size="sm" onClick={onRolesRefetch}>
            Повторить
          </Button>
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
            Управление ролью · secret_service
          </h3>
          <span className="text-xs text-dim">
            scope: <span className="mono">{departmentId ?? "—"}</span>
          </span>
        </div>
        <label className="flex flex-col gap-1 text-xs max-w-sm">
          <span className="text-dim">роль отдела</span>
          <Dropdown
            mode="single"
            searchable
            placeholder="— выберите роль —"
            options={allRoleNames.map((r): DropdownOption => ({
              value: r,
              label: `${r}${SYSTEM_ROLES.includes(r as SecretRoleName) ? " · system" : ""}`,
            }))}
            value={selected}
            onChange={setSelected}
          />
        </label>
        <p className="text-[11px] text-dim mt-2 leading-relaxed">
          Выберите роль, чтобы видеть и редактировать её права. Системные{" "}
          <span className="mono">admin/guest</span> залочены, удалять можно
          только кастомные роли.
        </p>
      </div>

      {!selected ? (
        <div className="empty-card text-sm text-dim">Роль не выбрана.</div>
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

          {!isLocked && (
            <div className="sticky top-0 z-30 flex items-center gap-2 flex-wrap border border-token rounded bg-[var(--bg-soft)] px-3 py-2">
              <Button variant="primary" size="sm"
                className="flex items-center gap-1"
                onClick={() => onSaveRole(selected)}
                disabled={saving || roleDirtyCount(selected) === 0}
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Check className="w-3.5 h-3.5" />
                )}
                Сохранить роль
                {roleDirtyCount(selected) > 0 &&
                  ` (${roleDirtyCount(selected)})`}
              </Button>
              <Button size="sm"
                onClick={() => onCancelRole(selected)}
                disabled={saving || roleDirtyCount(selected) === 0}
              >
                Отмена
              </Button>
              <Button variant="danger" size="sm"
                className="flex items-center gap-1"
                onClick={() => onClearRoleEverywhere(selected)}
                disabled={saving}
                title="Снять все права этой роли (применится по «Сохранить роль»)"
              >
                <Trash2 className="w-3.5 h-3.5" /> Очистить во всех таблицах
              </Button>
              <span className="text-[11px] text-dim ml-auto">
                {roleDirtyCount(selected) > 0
                  ? `Несохранённых изменений: ${roleDirtyCount(selected)}`
                  : "Изменения применяются по кнопке «Сохранить роль»"}
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
      await patchServiceRole(departmentId, SECRET_SERVICE, roleName, {
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
      await deleteServiceRole(departmentId, SECRET_SERVICE, roleName);
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
              <Badge className="text-[10px] flex items-center gap-1">
                <Lock className="w-3 h-3" /> system
              </Badge>
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
            <Button variant="ghost" size="sm"
              className="flex items-center gap-1"
              onClick={startEdit}
              disabled={busy}
            >
              <Pencil className="w-3 h-3" /> описание
            </Button>
          )}
          {canDelete && (
            <Button variant="danger" size="sm"
              className="flex items-center gap-1"
              onClick={onDelete}
              disabled={busy}
            >
              {busy ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Trash2 className="w-3 h-3" />
              )}
              удалить
            </Button>
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
          <Button variant="primary" size="sm"
            className="flex items-center gap-1"
            onClick={saveDesc}
            disabled={busy}
          >
            {busy ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Check className="w-3 h-3" />
            )}
            сохранить
          </Button>
          <Button variant="ghost" size="sm"
            className="flex items-center gap-1"
            onClick={() => setEditing(false)}
            disabled={busy}
          >
            <X className="w-3 h-3" /> отмена
          </Button>
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
  entity: SecretPermissionCatalogItem;
  role: SecretRoleName;
  locked: boolean;
  isAllowed: IsAllowedFn;
  saving: boolean;
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
            const allowed = isAllowed(role, a.action);
            const disabled = locked || saving;
            const reason = locked
              ? "роль залочена"
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
                  saving={false}
                  title={`${a.description}\n\n${reason}`}
                  onClick={() => onToggle(role, a)}
                />
                <span className="mono text-xs flex items-center gap-1">
                  {a.action}
                  {a.sensitive && (
                    <Badge className="text-[9px]">sensitive</Badge>
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
  baseRoles: SecretRoleName[];
  onCreated: (
    roleName: SecretRoleName,
    base: SecretRoleName | null,
  ) => void | Promise<void>;
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
      await createServiceRole(departmentId, SECRET_SERVICE, {
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
        <Button variant="ghost"
          className="text-xs flex items-center gap-1"
          onClick={() => setOpen(true)}
        >
          <Plus className="w-3 h-3" /> Новая роль
        </Button>
      </div>
    );
  }

  return (
    <div className="mt-3 pt-3 border-t border-token">
      <div className="p-3 border border-token rounded">
        <div className="text-sm font-semibold mb-2 flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" />
            Новая роль · secret_service
          </span>
          <Button variant="ghost"
            className="text-xs flex items-center gap-1"
            onClick={reset}
            disabled={busy}
          >
            <X className="w-3 h-3" /> закрыть
          </Button>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-dim">имя роли</span>
            <input
              className="input mono"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="secret_reader"
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-dim">на основе (опционально)</span>
            <Dropdown
              mode="single"
              searchable
              placeholder="— с нуля —"
              options={baseRoles.map((r): DropdownOption => ({ value: r, label: r }))}
              value={base}
              onChange={setBase}
              disabled={busy}
            />
          </label>
          <Button variant="primary"
            className="flex items-center gap-1"
            onClick={submit}
            disabled={busy || !name.trim()}
          >
            {busy && <Loader2 className="w-3 h-3 animate-spin" />}
            Создать
          </Button>
        </div>
        <p className="text-[10px] text-dim mt-2 leading-relaxed">
          Роль создаётся в каталоге{" "}
          <span className="mono">(отдел, secret_service)</span>. С базовой ролью
          её grant'ы копируются в новую — дальше правьте чекбоксами. Системные
          роли (<span className="mono">guest/admin</span>) дублировать нельзя.
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
  dirtyCount,
  onSave,
  onCancel,
  onClearRole,
  onClearAllRoles,
  hasOverride,
}: {
  entity: SecretPermissionCatalogItem;
  roles: SecretRoleName[];
  isAllowed: IsAllowedFn;
  saving: boolean;
  onToggle: ToggleFn;
  dirtyCount: number;
  onSave: () => void;
  onCancel: () => void;
  onClearRole: (role: SecretRoleName) => void;
  onClearAllRoles: () => void;
  hasOverride: (role: SecretRoleName) => boolean;
}) {
  // Роли, которые можно чистить в таблице (залоченные исключены).
  const clearableRoles = useMemo(
    () => roles.filter((r) => !(r in LOCKED_ROLES)),
    [roles],
  );
  const [selectedRole, setSelectedRole] = useState<SecretRoleName | "">("");
  const activeRole: SecretRoleName | "" =
    selectedRole && clearableRoles.includes(selectedRole)
      ? selectedRole
      : (clearableRoles[0] ?? "");
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
        <>
          <div className="overflow-auto max-h-[60vh] border border-token rounded">
            <table className="text-sm border-collapse">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pt-2 px-3 sticky left-0 top-0 z-20 bg-[var(--bg-soft)]">
                    роль
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
                          <Badge className="text-[9px]">!</Badge>
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
                        const allowed = isAllowed(role, a.action);
                        const disabled = locked || saving;
                        const reason = locked
                          ? LOCKED_ROLES[role]
                          : allowed
                            ? "Снять — отозвать"
                            : "Поставить — выдать";
                        return (
                          <td
                            key={a.action}
                            className="py-1.5 px-2 text-center"
                          >
                            <PermCheckbox
                              allowed={allowed}
                              disabled={disabled}
                              saving={false}
                              title={`${a.description}\n\n${reason}`}
                              onClick={() => onToggle(role, a)}
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

          <div className="sticky bottom-0 z-30 mt-3 -mb-1 flex items-center gap-2 flex-wrap border-t border-token bg-[var(--bg-soft)] pt-3 pb-2">
            <Button variant="primary" size="sm"
              className="flex items-center gap-1"
              onClick={onSave}
              disabled={saving || dirtyCount === 0}
            >
              {saving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5" />
              )}
              Сохранить
              {dirtyCount > 0 && ` (${dirtyCount})`}
            </Button>
            <Button size="sm"
              onClick={onCancel}
              disabled={saving || dirtyCount === 0}
            >
              Отмена
            </Button>
            <span className="mx-1 h-5 w-px bg-token" aria-hidden="true" />
            <Dropdown
              mode="single"
              searchable
              options={clearableRoles.map((r): DropdownOption => ({ value: r, label: r }))}
              value={activeRole}
              onChange={(v) => setSelectedRole(v as SecretRoleName)}
              disabled={saving || clearableRoles.length === 0}
            />
            <Button size="sm"
              className="flex items-center gap-1"
              onClick={() => activeRole && onClearRole(activeRole)}
              disabled={saving || !activeRole || !hasOverride(activeRole)}
              title="Снять все права выбранной роли в этой таблице (применится по «Сохранить»)"
            >
              <RotateCcw className="w-3.5 h-3.5" /> Очистить роль
            </Button>
            <Button size="sm"
              className="flex items-center gap-1"
              onClick={onClearAllRoles}
              disabled={saving || clearableRoles.every((r) => !hasOverride(r))}
              title="Снять все права всех ролей в этой таблице (применится по «Сохранить»)"
            >
              <Trash2 className="w-3.5 h-3.5" /> Очистить все роли
            </Button>
            <span className="text-[11px] text-dim ml-auto">
              {dirtyCount > 0
                ? `Несохранённых изменений: ${dirtyCount}`
                : "Изменения применяются по кнопке «Сохранить»"}
            </span>
          </div>
        </>
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
    <Checkbox
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
