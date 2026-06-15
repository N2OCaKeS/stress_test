import { useCallback, useMemo, useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  Lock,
  Bot,
  Loader2,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import {
  getPermissionCatalog,
  listPermissions,
  setPermission,
  deletePermission,
} from "@/api/server/permissions";
import { apiErrMsg } from "@/api/client";
import { personaDeptId } from "@/lib/rbac";
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
 * (`entity_type`); внутри — таблица роли × действия, каждая ячейка — тоггл,
 * клик по которому мгновенно выдаёт/отзывает grant через PUT/DELETE. Правка
 * оптимистична: ячейка перекрашивается сразу, при ошибке backend'а откат +
 * тост.
 *
 * Строки `admin` и `guest` залочены: admin держит полный доступ, guest —
 * базовый, их правила менять нельзя. Редактируются `reader`, `operator` и
 * кастомные роли отдела.
 *
 * RBAC: матрица — business-data `server_service`. Backend пускает носителя
 * `(permission, *, view/grant/revoke)`: department_admin своего отдела или
 * сервисную роль `server.admin`. Platform-админы режутся middleware'ом 403
 * PLATFORM_ADMIN_BUSINESS_DATA_DENIED — им страница не показывается.
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

// Порядок системных ролей в таблице; кастомные идут после по алфавиту.
const SYSTEM_ROLE_ORDER: RoleName[] = ["guest", "reader", "operator", "admin"];

function roleSortKey(role: RoleName): string {
  const idx = SYSTEM_ROLE_ORDER.indexOf(role);
  // Системные роли — фиксированный порядок (0..3), кастомные — после, по имени.
  return idx >= 0 ? `0${idx}` : `1${role}`;
}

type CellStatus = "idle" | "saving";

function ServicesServerPermissionsLive() {
  const { persona } = usePersona();
  const toast = useToast();
  const myDept = personaDeptId(persona);

  const [refreshTick, setRefreshTick] = useState(0);
  const bump = useCallback(() => setRefreshTick((t) => t + 1), []);

  const catalogQ = useQuery(() => getPermissionCatalog(), []);
  const grantsQ = useQuery(
    () => listPermissions({ describe: true }),
    [refreshTick],
  );

  const catalog = catalogQ.data ?? [];
  const grants = grantsQ.data?.items ?? [];

  // Оптимистичный слой: ключ `entity::role::action` → true (allow) | false
  // (revoked). Перетирает то, что пришло с сервера, до следующего refetch'а.
  const [optimistic, setOptimistic] = useState<Record<string, boolean>>({});
  // Какие ячейки сейчас в полёте — чтобы рисовать спиннер и блокировать клик.
  const [saving, setSaving] = useState<Record<string, CellStatus>>({});

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

  // Роли таблицы для конкретной сущности: системные всегда + любые кастомные
  // из существующих grant'ов этой сущности.
  const rolesFor = useCallback(
    (entity: EntityType): RoleName[] => {
      const set = new Set<RoleName>(SYSTEM_ROLE_ORDER);
      for (const g of grants) {
        if (g.entity_type === entity) set.add(g.role);
      }
      return Array.from(set).sort((a, b) =>
        roleSortKey(a).localeCompare(roleSortKey(b)),
      );
    },
    [grants],
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
          Клик по ячейке мгновенно выдаёт или отзывает действие для роли.
          Строки <span className="mono">admin</span> и{" "}
          <span className="mono">guest</span> залочены. Изменения пишутся в
          рамках вашего отдела (<span className="mono">{myDept ?? "—"}</span>).
        </p>
        <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim flex items-center gap-4 flex-wrap">
          <span className="flex items-center gap-1">
            <span className="inline-block w-7 h-4 rounded-full bg-[var(--accent)]" />{" "}
            allow
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-7 h-4 rounded-full border border-token" />{" "}
            default (нет grant'а)
          </span>
          <span className="flex items-center gap-1">
            <Lock className="w-3 h-3" /> роль залочена
          </span>
          <span className="flex items-center gap-1">
            <span className="badge badge-warn text-[10px]">!</span> sensitive →
            CRITICAL audit
          </span>
          <span className="flex items-center gap-1">
            <Bot className="w-3 h-3" /> worker_only — людям не выдаётся
          </span>
        </div>
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
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3 sticky left-0">role</th>
                {entity.actions.map((a) => (
                  <th
                    key={a.action}
                    className="pb-2 px-2 mono font-normal align-bottom"
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
                    <td className="py-2 pr-3 mono text-xs sticky left-0">
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
                            ? "Кликни — отозвать"
                            : "Кликни — выдать";
                      return (
                        <td key={a.action} className="py-1.5 px-2">
                          <PermToggle
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
// Toggle cell
// ---------------------------------------------------------------------------

function PermToggle({
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
  const track = [
    "relative inline-flex items-center w-9 h-5 rounded-full border transition-colors",
    allowed
      ? "bg-[var(--accent)] border-[var(--accent)]"
      : "bg-transparent border-token",
    disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
    saving ? "cursor-wait" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const knob = [
    "inline-flex items-center justify-center w-4 h-4 rounded-full bg-[var(--bg-soft-2)] shadow transition-transform",
    allowed ? "translate-x-[18px]" : "translate-x-0.5",
  ].join(" ");
  return (
    <button
      type="button"
      role="switch"
      aria-checked={allowed}
      className={track}
      disabled={disabled || saving}
      title={title}
      onClick={onClick}
    >
      <span className={knob}>
        {saving && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
      </span>
    </button>
  );
}
