import { useCallback, useMemo, useState } from "react";
import {
  ShieldCheck,
  Check,
  X,
  Building2,
  AlertTriangle,
  XCircle,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import {
  getPermissionCatalog,
  listPermissions,
  setPermission,
  deletePermission,
} from "@/api/server/permissions";
import { listDepartments } from "@/api/auth/departments";
import { ApiError, apiErrMsg } from "@/api/client";
import type {
  ActionName,
  EntityType,
  PermissionCatalogAction,
  PermissionCatalogItem,
  PermissionEntry,
  RoleName,
} from "@/api/server/types";
import type { Department } from "@/api/auth/types";

/**
 * Матрица разрешений `server_service`. Строки таблицы — роли (из текущих
 * grant'ов), столбцы — действия выбранной сущности. Ячейка показывает
 * эффективное состояние: allow (✓), default (·) или revoked (—).
 *
 * RBAC: только `account_admin`. Реальные мутации в backend'е дополнительно
 * фильтруются middleware (см. wrapper'ы permissions.ts), но UI-страница —
 * единая для всей платформы.
 */
export function ServicesServerPermissions() {
  const { persona } = usePersona();
  if (persona.platform_role !== "account_admin") {
    return (
      <div className="p-8">
        <div className="alert-danger flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          <span>
            403 · доступ к матрице разрешений `server_service` есть только у
            <span className="mono"> account_admin</span>.
          </span>
        </div>
      </div>
    );
  }
  return <ServicesServerPermissionsLive />;
}

function ServicesServerPermissionsLive() {
  const [refreshTick, setRefreshTick] = useState(0);
  const bump = useCallback(() => setRefreshTick((t) => t + 1), []);

  const catalogQ = useQuery(() => getPermissionCatalog(), []);
  const grantsQ = useQuery(
    () => listPermissions({ describe: true }),
    [refreshTick],
  );
  const deptsQ = useQuery(() => listDepartments(), []);

  const catalog = catalogQ.data ?? [];
  const grants = grantsQ.data?.items ?? [];
  const depts = deptsQ.data ?? [];

  const [entityType, setEntityType] = useState<EntityType | "">("");

  // Подтянуть первый entity по умолчанию, когда каталог загрузится.
  const effectiveEntity: EntityType | "" = useMemo(() => {
    if (entityType) return entityType;
    return catalog[0]?.entity_type ?? "";
  }, [entityType, catalog]);

  const entity: PermissionCatalogItem | undefined = useMemo(
    () => catalog.find((c) => c.entity_type === effectiveEntity),
    [catalog, effectiveEntity],
  );

  // Все роли с грантами на выбранную сущность — это строки таблицы.
  const rolesForEntity: RoleName[] = useMemo(() => {
    if (!effectiveEntity) return [];
    const set = new Set<RoleName>();
    for (const g of grants) {
      if (g.entity_type === effectiveEntity) set.add(g.role);
    }
    // Системные роли всегда показываем, чтобы матрица не была пустой даже
    // когда в БД нет ни одного per-dept грант'а — пользователь сможет
    // выдать первое разрешение прямо отсюда.
    for (const r of ["guest", "reader", "operator", "admin"] as RoleName[]) {
      set.add(r);
    }
    return Array.from(set).sort();
  }, [grants, effectiveEntity]);

  // Индекс gran'тов по (role, action) для O(1) lookup'а из ячеек.
  const grantIndex = useMemo(() => {
    const m = new Map<string, PermissionEntry>();
    if (!effectiveEntity) return m;
    for (const g of grants) {
      if (g.entity_type !== effectiveEntity) continue;
      m.set(`${g.role}::${g.action}`, g);
    }
    return m;
  }, [grants, effectiveEntity]);

  const [modalCell, setModalCell] = useState<{
    role: RoleName;
    action: PermissionCatalogAction;
    current: PermissionEntry | null;
  } | null>(null);

  if (catalogQ.loading || grantsQ.loading) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="spinner">Загрузка матрицы…</div>
      </div>
    );
  }

  if (catalogQ.error || grantsQ.error) {
    const err = catalogQ.error ?? grantsQ.error;
    const msg =
      err instanceof ApiError ? `${err.errorCode}: ${err.message}` : err?.message;
    return (
      <div className="flex-1 p-8">
        <div className="alert-danger flex items-center gap-2">
          <span>{msg}</span>
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
            grants · {grants.length} строк
          </span>
        </div>

        <div className="flex flex-wrap gap-3 items-center text-sm">
          <label className="flex items-center gap-2">
            <span className="text-dim text-xs uppercase">Entity</span>
            <select
              className="input input-sm mono"
              value={effectiveEntity}
              onChange={(e) => setEntityType(e.target.value as EntityType)}
            >
              {catalog.map((c) => (
                <option key={c.entity_type} value={c.entity_type}>
                  {c.entity_type}
                </option>
              ))}
            </select>
          </label>
          {entity && (
            <span className="text-xs text-dim">{entity.description}</span>
          )}
        </div>
      </div>

      {entity ? (
        <PermissionMatrix
          entity={entity}
          roles={rolesForEntity}
          grantIndex={grantIndex}
          onCellClick={(role, action) => {
            const current = grantIndex.get(`${role}::${action.action}`) ?? null;
            setModalCell({ role, action, current });
          }}
        />
      ) : (
        <div className="empty-card text-sm text-dim">
          Каталог пуст — backend не вернул ни одной сущности.
        </div>
      )}

      {modalCell && entity && (
        <PermissionCellModal
          entityType={entity.entity_type}
          role={modalCell.role}
          action={modalCell.action}
          current={modalCell.current}
          depts={depts}
          onClose={() => setModalCell(null)}
          onSaved={() => {
            setModalCell(null);
            bump();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Matrix table
// ---------------------------------------------------------------------------

function PermissionMatrix({
  entity,
  roles,
  grantIndex,
  onCellClick,
}: {
  entity: PermissionCatalogItem;
  roles: RoleName[];
  grantIndex: Map<string, PermissionEntry>;
  onCellClick: (role: RoleName, action: PermissionCatalogAction) => void;
}) {
  if (entity.actions.length === 0) {
    return (
      <div className="empty-card text-sm text-dim">
        У сущности `{entity.entity_type}` нет действий в каталоге.
      </div>
    );
  }
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-dim text-xs uppercase">
          <tr>
            <th className="pb-2 pr-3">role / action</th>
            {entity.actions.map((a) => (
              <th
                key={a.action}
                className="pb-2 pr-3 mono"
                title={a.description}
              >
                <div className="flex items-center gap-1">
                  <span>{a.action}</span>
                  {a.sensitive && (
                    <span className="badge badge-warn text-[10px]">!</span>
                  )}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role} className="border-t border-token">
              <td className="py-2 pr-3 mono text-xs">{role}</td>
              {entity.actions.map((a) => {
                const g = grantIndex.get(`${role}::${a.action}`);
                return (
                  <td key={a.action} className="py-2 pr-3">
                    <button
                      className="text-left hover:opacity-80"
                      title={`${a.description}\n\n${g ? "allow" : "default"}`}
                      onClick={() => onCellClick(role, a)}
                    >
                      <CellState entry={g} />
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim flex items-center gap-4 flex-wrap">
        <span className="flex items-center gap-1">
          <Check className="w-3 h-3 text-accent" /> allow (явный grant)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 text-dim text-center">·</span> default (из
          каталога)
        </span>
        <span className="flex items-center gap-1">
          <span className="badge badge-warn text-[10px]">!</span> sensitive →
          CRITICAL audit
        </span>
      </div>
    </div>
  );
}

function CellState({ entry }: { entry: PermissionEntry | undefined }) {
  if (entry) {
    return (
      <span className="badge badge-accent flex items-center gap-1 w-fit">
        <Check className="w-3 h-3" />
        {entry.department_id ? "dept" : "system"}
      </span>
    );
  }
  return <span className="text-dim text-sm">·</span>;
}

// ---------------------------------------------------------------------------
// Modal — grant / revoke single cell
// ---------------------------------------------------------------------------

function PermissionCellModal({
  entityType,
  role,
  action,
  current,
  depts,
  onClose,
  onSaved,
}: {
  entityType: EntityType;
  role: RoleName;
  action: PermissionCatalogAction;
  current: PermissionEntry | null;
  depts: Department[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [targetDept, setTargetDept] = useState<string>(
    current?.department_id ?? "",
  );
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const doGrant = async () => {
    setErr(null);
    setPending(true);
    try {
      await setPermission(entityType, role, action.action as ActionName, {
        target_department_id: targetDept || undefined,
      });
      onSaved();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

  const doRevoke = async () => {
    if (!current) return;
    if (
      !confirm(
        `Сбросить grant ${entityType}/${role}/${action.action} к дефолту каталога?`,
      )
    )
      return;
    setErr(null);
    setPending(true);
    try {
      await deletePermission(entityType, role, action.action as ActionName, {
        target_department_id: current.department_id ?? undefined,
      });
      onSaved();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

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
          <h4 className="font-semibold flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" />
            <span className="mono text-sm">
              {entityType} / {role} / {action.action}
            </span>
          </h4>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            <XCircle className="w-4 h-4" />
          </button>
        </div>

        <div className="text-xs text-dim mb-3">{action.description}</div>

        {action.sensitive && (
          <div className="alert-block mb-3 text-xs flex items-center gap-2">
            <AlertTriangle className="w-3 h-3" />
            Sensitive action — будет записано в audit с severity CRITICAL.
          </div>
        )}

        <div className="text-xs uppercase text-dim mb-2">Текущее состояние</div>
        <div className="text-sm mb-4">
          {current ? (
            <div className="flex items-center gap-2">
              <span className="badge badge-accent flex items-center gap-1">
                <Check className="w-3 h-3" /> allow
              </span>
              <span className="text-dim text-xs">
                {current.department_id ? (
                  <>
                    dept ·{" "}
                    {depts.find((d) => d.id === current.department_id)?.name ??
                      current.department_id}{" "}
                    <span className="mono">({current.department_id})</span>
                  </>
                ) : (
                  "system-wide"
                )}
              </span>
            </div>
          ) : (
            <span className="text-dim">default (из каталога — нет явного grant'а)</span>
          )}
        </div>

        {err && <div className="alert-danger mb-3 text-xs">{err}</div>}

        <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
          <Building2 className="w-3 h-3" /> target_department_id
        </div>
        <select
          className="input mono mb-3"
          value={targetDept}
          onChange={(e) => setTargetDept(e.target.value)}
        >
          <option value="">— system-wide (без отдела) —</option>
          {depts.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} · {d.id}
            </option>
          ))}
        </select>
        <div className="text-[11px] text-dim mb-4">
          Backend требует, чтобы `target_department_id` совпадал с
          `department_id` caller'а либо был пуст. Несовпадение → 403
          DEPARTMENT_ISOLATION.
        </div>

        <div className="flex gap-2 justify-end">
          <button className="btn" onClick={onClose} disabled={pending}>
            Закрыть
          </button>
          {current && (
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={pending}
              onClick={doRevoke}
              title="Сбросить к дефолту каталога"
            >
              <X className="w-4 h-4" /> Revoke
            </button>
          )}
          <button
            className="btn btn-primary flex items-center gap-1"
            disabled={pending || action.worker_only}
            onClick={doGrant}
            title={
              action.worker_only
                ? "worker_only — выдавать людям нельзя"
                : "Выдать grant"
            }
          >
            <Check className="w-4 h-4" /> Allow
          </button>
        </div>
      </div>
    </div>
  );
}
