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
  RotateCcw,
  Crosshair,
  Server as ServerIcon,
  KeyRound,
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
import { listServers } from "@/api/server/servers";
import { listAccounts } from "@/api/server/accounts";
import { apiErrMsg } from "@/api/client";
import { personaDeptId } from "@/lib/rbac";
import {
  ResourceInstancePermissions,
  type ResourceTargetOption,
} from "@/pages/server/_resourcePermissions";
import type { ServiceRole } from "@/api/auth/types";
import type {
  ActionName,
  EntityType,
  PermissionCatalogAction,
  PermissionCatalogItem,
  PermissionEntry,
  ResourceAclType,
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
 * только кастомные роли отдела. Кастомную роль можно завести
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
const SYSTEM_ROLES: RoleName[] = ["guest", "admin"];

// Порядок системных ролей в таблице; кастомные идут после по алфавиту.
const SYSTEM_ROLE_ORDER: RoleName[] = ["guest", "admin"];

// Внутренние роли, которые в матрицу не выводим вообще (гранты на backend у
// них остаются — это чисто UI-скрытие).
const HIDDEN_ROLES: ReadonlySet<RoleName> = new Set<RoleName>(["worker_bot"]);

function roleSortKey(role: RoleName): string {
  const idx = SYSTEM_ROLE_ORDER.indexOf(role);
  // Системные роли — фиксированный порядок, кастомные — после, по имени.
  return idx >= 0 ? `0${idx}` : `1${role}`;
}

type ViewMode = "matrix" | "role";

function ServicesServerPermissionsLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const myDept = personaDeptId(persona);
  const canEdit =
    persona.platform_role === "dep_admin" ||
    persona.service_roles?.server === "admin";

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

  const catalog = useMemo(() => catalogQ.data ?? [], [catalogQ.data]);
  const grants = useMemo(() => grantsQ.data?.items ?? [], [grantsQ.data]);
  const serviceRoles = rolesQ.data ?? [];

  // Локальные правки: ключ `entity::role::action` → желаемое allow (true) /
  // revoked (false). Копятся до батч-сохранения кнопкой нужной таблицы/роли.
  const [edits, setEdits] = useState<Record<string, boolean>>({});
  // Идёт ли сейчас применение диффа — на это время контролы блокируем.
  const [saving, setSaving] = useState(false);
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

  const serverAllowed = useCallback(
    (key: string): boolean => serverIndex.has(key),
    [serverIndex],
  );

  // Эффективное состояние ячейки: локальная правка важнее серверной.
  const isAllowed = useCallback(
    (entity: EntityType, role: RoleName, action: ActionName): boolean => {
      const key = `${entity}::${role}::${action}`;
      if (key in edits) return edits[key];
      return serverAllowed(key);
    },
    [edits, serverAllowed],
  );

  // Все кастомные роли отдела: из существующих grant'ов + созданные локально.
  // Внутренние роли (worker_bot) не показываем вовсе.
  const customRoles = useMemo(() => {
    const set = new Set<RoleName>();
    for (const g of grants) {
      if (!SYSTEM_ROLES.includes(g.role)) set.add(g.role);
    }
    for (const r of localRoles) set.add(r);
    for (const h of HIDDEN_ROLES) set.delete(h);
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

  // Клик по ячейке только копит правку — без сети.
  const toggleCell = useCallback(
    (entity: EntityType, role: RoleName, action: PermissionCatalogAction) => {
      if (role in LOCKED_ROLES) return;
      if (action.worker_only) return;
      const key = `${entity}::${role}::${action.action}`;
      setEdits((m) => ({ ...m, [key]: !isAllowed(entity, role, action.action) }));
    },
    [isAllowed],
  );

  // Снять все права роли в одной таблице (этом entity_type) — стейдж revoke.
  const clearRoleInEntity = useCallback(
    (entity: PermissionCatalogItem, role: RoleName) => {
      if (role in LOCKED_ROLES) return;
      setEdits((m) => {
        const next = { ...m };
        for (const a of entity.actions) {
          if (a.worker_only) continue;
          next[`${entity.entity_type}::${role}::${a.action}`] = false;
        }
        return next;
      });
    },
    [],
  );

  // Снять все права роли во ВСЕХ таблицах сразу — стейдж revoke по каталогу.
  const clearRoleEverywhere = useCallback(
    (role: RoleName) => {
      if (role in LOCKED_ROLES) return;
      setEdits((m) => {
        const next = { ...m };
        for (const entity of catalog) {
          for (const a of entity.actions) {
            if (a.worker_only) continue;
            next[`${entity.entity_type}::${role}::${a.action}`] = false;
          }
        }
        return next;
      });
    },
    [catalog],
  );

  // Ключи диффа (правка != сервер) среди заданного подмножества.
  const diffKeys = useCallback(
    (predicate: (key: string) => boolean): string[] =>
      Object.keys(edits).filter(
        (k) => predicate(k) && edits[k] !== serverAllowed(k),
      ),
    [edits, serverAllowed],
  );

  const entityDiffKeys = useCallback(
    (entity: EntityType) => diffKeys((k) => k.startsWith(`${entity}::`)),
    [diffKeys],
  );

  const roleDiffKeys = useCallback(
    (role: RoleName) => diffKeys((k) => k.split("::")[1] === role),
    [diffKeys],
  );

  // Применить дифф по списку ключей: PUT для allow, DELETE для revoke.
  const applyDiff = useCallback(
    async (keys: string[]) => {
      if (saving || keys.length === 0) return;
      setSaving(true);
      const done: string[] = [];
      let ok = 0;
      let failed = 0;
      for (const key of keys) {
        const [entity, role, action] = key.split("::") as [
          EntityType,
          RoleName,
          ActionName,
        ];
        try {
          if (edits[key]) {
            await setPermission(entity, role, action, {
              target_department_id: myDept || undefined,
            });
          } else {
            await deletePermission(entity, role, action, {
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
                Глобальные (тип-wide) правила · server_service
              </h3>
              <span className="text-xs text-dim">
                {grants.length} grant-строк · scope:{" "}
                <span className="mono">{myDept ?? "—"}</span>
              </span>
            </div>
            <p className="text-xs text-dim leading-relaxed">
              Правила на весь <em>тип</em> сущности (все серверы / все учётки).
              Клик по чекбоксу копит правку локально; применяется батчем кнопкой
              «Сохранить» у нужной таблицы. Строки{" "}
              <span className="mono">admin</span> и{" "}
              <span className="mono">guest</span> залочены. Изменения пишутся в
              рамках вашего отдела (
              <span className="mono">{myDept ?? "—"}</span>).
            </p>
            <p className="text-[11px] text-dim leading-relaxed mt-2">
              Точечные права на <em>конкретный</em> сервер или учётку (поверх
              этих глобальных) настраиваются на самом ресурсе — вкладка «Права»
              карточки сервера и блок «Инстанс-гранты» в деталях учётки.
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
            catalog.map((entity) => {
              const dkeys = entityDiffKeys(entity.entity_type);
              return (
                <EntityMatrix
                  key={entity.entity_type}
                  entity={entity}
                  roles={rolesFor(entity.entity_type)}
                  isAllowed={isAllowed}
                  saving={saving}
                  onToggle={toggleCell}
                  dirtyCount={dkeys.length}
                  onSave={() => applyDiff(dkeys)}
                  onCancel={() =>
                    cancelKeys(
                      allKeysIn((k) =>
                        k.startsWith(`${entity.entity_type}::`),
                      ),
                    )
                  }
                  onClearRole={(role) => clearRoleInEntity(entity, role)}
                  hasOverride={(role) =>
                    entity.actions.some(
                      (a) =>
                        !a.worker_only &&
                        isAllowed(entity.entity_type, role, a.action),
                    )
                  }
                />
              );
            })
          )}

          <InstancePointSection departmentId={myDept} canEdit={canEdit} />
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
            cancelKeys(allKeysIn((k) => k.split("::")[1] === role))
          }
          onClearRoleEverywhere={clearRoleEverywhere}
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
  roleDirtyCount,
  onSaveRole,
  onCancelRole,
  onClearRoleEverywhere,
}: {
  departmentId: string | null;
  catalog: PermissionCatalogItem[];
  serviceRoles: ServiceRole[];
  rolesLoading: boolean;
  rolesError: Error | null;
  onRolesRefetch: () => void;
  isAllowed: IsAllowedFn;
  saving: boolean;
  onToggle: ToggleFn;
  onRolesChanged: () => void;
  roleDirtyCount: (role: RoleName) => number;
  onSaveRole: (role: RoleName) => void;
  onCancelRole: (role: RoleName) => void;
  onClearRoleEverywhere: (role: RoleName) => void;
}) {
  const [selected, setSelected] = useState<string>("");

  // Системные guest/admin не приходят в service_roles (он хранит только
  // кастомные определения отдела), поэтому добиваем их вручную. Внутреннюю
  // worker_bot не показываем.
  const allRoleNames = useMemo(() => {
    const set = new Set<string>(SYSTEM_ROLE_ORDER);
    for (const r of serviceRoles) set.add(r.role_name);
    for (const h of HIDDEN_ROLES) set.delete(h);
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

          {!isLocked && (
            <div className="sticky top-0 z-30 flex items-center gap-2 flex-wrap border border-token rounded bg-[var(--bg-soft)] px-3 py-2">
              <button
                className="btn btn-sm btn-primary flex items-center gap-1"
                onClick={() => onSaveRole(selected)}
                disabled={saving || roleDirtyCount(selected) === 0}
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Check className="w-3.5 h-3.5" />
                )}
                Сохранить роль
                {roleDirtyCount(selected) > 0 && ` (${roleDirtyCount(selected)})`}
              </button>
              <button
                className="btn btn-sm"
                onClick={() => onCancelRole(selected)}
                disabled={saving || roleDirtyCount(selected) === 0}
              >
                Отмена
              </button>
              <button
                className="btn btn-sm btn-danger flex items-center gap-1"
                onClick={() => onClearRoleEverywhere(selected)}
                disabled={saving}
                title="Снять все права этой роли во всех таблицах (применится по «Сохранить роль»)"
              >
                <Trash2 className="w-3.5 h-3.5" /> Очистить во всех таблицах
              </button>
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
            const allowed = isAllowed(entity.entity_type, role, a.action);
            const disabled = locked || a.worker_only || saving;
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
                  saving={false}
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
          (<span className="mono">guest/admin</span>)
          дублировать нельзя.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Точечные права на конкретный ресурс (поверх тип-wide базы)
// ---------------------------------------------------------------------------

// Блок под базовой матрицей: выбираешь конкретный сервер или учётку и правишь
// allow/deny поверх «стандартной» (тип-wide) матрицы. Базовая матрица сверху
// остаётся стандартом для всех серверов отдела, включая будущие.
function InstancePointSection({
  departmentId,
  canEdit,
}: {
  departmentId: string | null;
  canEdit: boolean;
}) {
  const [resourceType, setResourceType] = useState<ResourceAclType>("server");
  const [resourceId, setResourceId] = useState<string>("");

  const serversQ = useQuery(
    () => listServers({ department_id: departmentId ?? undefined, limit: 200 }),
    [departmentId],
  );
  const accountsQ = useQuery(() => listAccounts({ limit: 200 }), []);

  const serverOptions: ResourceTargetOption[] = useMemo(
    () =>
      (serversQ.data?.items ?? []).map((s) => ({
        id: s.id,
        label: s.display_name ?? s.hostname,
      })),
    [serversQ.data],
  );
  const accountOptions: ResourceTargetOption[] = useMemo(
    () =>
      (accountsQ.data?.items ?? []).map((a) => ({
        id: a.id,
        label: a.login,
      })),
    [accountsQ.data],
  );

  const options =
    resourceType === "server" ? serverOptions : accountOptions;
  const loading = resourceType === "server" ? serversQ.loading : accountsQ.loading;
  const selectedLabel = options.find((o) => o.id === resourceId)?.label;

  function switchType(next: ResourceAclType) {
    setResourceType(next);
    setResourceId("");
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2">
          <Crosshair className="w-4 h-4 text-accent" />
          Точечные права на конкретный ресурс
        </h3>
        <span className="text-xs text-dim">поверх тип-wide базы выше</span>
      </div>
      <p className="text-xs text-dim leading-relaxed mb-3">
        Базовая матрица сверху — <em>стандарт для всех серверов и учёток отдела</em>{" "}
        (включая будущие). Здесь можно выбрать <em>конкретный</em> сервер или
        учётку и точечно <span className="mono">allow</span>/
        <span className="mono">deny</span> поверх этого стандарта: deny
        перекрывает базу и запрещает, allow — добавляет.
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-dim">тип ресурса</span>
          <div className="flex gap-1">
            <button
              type="button"
              className={[
                "btn btn-sm flex items-center gap-1",
                resourceType === "server" ? "btn-primary" : "",
              ].join(" ")}
              onClick={() => switchType("server")}
            >
              <ServerIcon className="w-3 h-3" /> сервер
            </button>
            <button
              type="button"
              className={[
                "btn btn-sm flex items-center gap-1",
                resourceType === "server_account" ? "btn-primary" : "",
              ].join(" ")}
              onClick={() => switchType("server_account")}
            >
              <KeyRound className="w-3 h-3" /> учётка
            </button>
          </div>
        </label>
        <label className="flex flex-col gap-1 text-xs flex-1 min-w-[220px]">
          <span className="text-dim">
            {resourceType === "server" ? "сервер" : "учётка"}
          </span>
          <select
            className="input"
            value={resourceId}
            onChange={(e) => setResourceId(e.target.value)}
            disabled={loading}
          >
            <option value="">
              {loading ? "— загрузка… —" : "— выберите ресурс —"}
            </option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {(serversQ.error || accountsQ.error) && (
        <div className="alert-danger text-xs mt-3">
          {apiErrMsg(serversQ.error ?? accountsQ.error, "Список не загрузился")}
        </div>
      )}

      {resourceId ? (
        <div className="mt-4">
          <ResourceInstancePermissions
            key={`${resourceType}:${resourceId}`}
            resourceType={resourceType}
            resourceId={resourceId}
            resourceLabel={selectedLabel}
            departmentId={departmentId}
            canEdit={canEdit}
            fetchTargets={async () =>
              resourceType === "server" ? serverOptions : accountOptions
            }
          />
        </div>
      ) : (
        <div className="empty-card text-sm text-dim mt-4">
          Ресурс не выбран — выберите сервер или учётку, чтобы задать точечные
          права поверх базы.
        </div>
      )}
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
  hasOverride,
}: {
  entity: PermissionCatalogItem;
  roles: RoleName[];
  isAllowed: (
    entity: EntityType,
    role: RoleName,
    action: ActionName,
  ) => boolean;
  saving: boolean;
  onToggle: (
    entity: EntityType,
    role: RoleName,
    action: PermissionCatalogAction,
  ) => void;
  dirtyCount: number;
  onSave: () => void;
  onCancel: () => void;
  onClearRole: (role: RoleName) => void;
  hasOverride: (role: RoleName) => boolean;
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
        <>
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
                          {!locked && (
                            <button
                              type="button"
                              className="btn btn-ghost p-0.5 disabled:opacity-30"
                              title="Очистить права роли в этой таблице"
                              aria-label={`Очистить роль ${role} в ${entity.entity_type}`}
                              disabled={saving || !hasOverride(role)}
                              onClick={() => onClearRole(role)}
                            >
                              <RotateCcw className="w-3 h-3 text-dim" />
                            </button>
                          )}
                        </span>
                      </td>
                      {entity.actions.map((a) => {
                        const allowed = isAllowed(
                          entity.entity_type,
                          role,
                          a.action,
                        );
                        const disabled = locked || a.worker_only || saving;
                        const reason = locked
                          ? LOCKED_ROLES[role]
                          : a.worker_only
                            ? "worker_only — выдавать людям нельзя"
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

          <div className="sticky bottom-0 z-30 mt-3 -mb-1 flex items-center gap-2 flex-wrap border-t border-token bg-[var(--bg-soft)] pt-3 pb-2">
            <button
              className="btn btn-sm btn-primary flex items-center gap-1"
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
            </button>
            <button
              className="btn btn-sm"
              onClick={onCancel}
              disabled={saving || dirtyCount === 0}
            >
              Отмена
            </button>
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
