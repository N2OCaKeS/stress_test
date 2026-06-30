/**
 * Инстанс-уровневый ACL ресурса (`server` / `server_account`).
 *
 * Поверх глобальной (тип-wide) матрицы `entity_permissions` тут редактируются
 * точечные гранты роли на КОНКРЕТНЫЙ ресурс: чекбокс на пересечении
 * роль × инстанс-грантуемое действие, клик ставит/снимает грант через PUT/DELETE.
 * Глобальные действия (`create` и callback'и воркера) сюда не попадают — они
 * живут только в глобальной матрице на странице админки.
 *
 * Кнопка «Распространить права» копирует все инстанс-гранты этого ресурса на
 * выбранные однотипные цели (merge — добавить недостающее; mirror — привести к
 * точной копии). Источник цели-списка — `fetchTargets` от родителя.
 *
 * Backend: server_service/src/api/v1/endpoints/resource_permissions.py.
 */
import { useCallback, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Ban,
  Check,
  Loader2,
  Lock,
  Minus,
  Share2,
  ShieldCheck,
} from "lucide-react";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { getPermissionCatalog } from "@/api/server/permissions";
import {
  grantResourcePermission,
  listResourcePermissions,
  propagateResourcePermissions,
  revokeResourcePermission,
} from "@/api/server/resourcePermissions";
import { listServiceRoles } from "@/api/auth/service_roles";
import {
  MOCK_INSTANCE_CATALOG,
  mockGrant,
  mockListResourcePermissions,
  mockPropagate,
  mockRevoke,
} from "@/mocks/resourcePermissions";
import { isInstanceGrantable } from "@/api/server/types";
import type {
  ActionName,
  PermissionCatalogAction,
  ResourceAclType,
  ResourcePermissionEffect,
  ResourcePermissionEntry,
  ResourcePropagateMode,
  ResourcePropagateResponse,
  RoleName,
} from "@/api/server/types";
import type { ServiceRole } from "@/api/auth/types";

// Системные роли в фиксированном порядке; кастомные идут после по алфавиту.
const SYSTEM_ROLE_ORDER: RoleName[] = ["guest", "admin"];

// Роли, чьи права фиксированы платформой — их нельзя трогать ни тут, ни на
// backend (вернёт 409 SYSTEM_ROLE_IMMUTABLE). Контролы для них read-only.
const LOCKED_ROLES: Record<string, string> = {
  admin: "admin держит полный доступ — правила не редактируются",
  guest: "guest — базовая роль, правила не редактируются",
};

// Состояние ячейки: null — не задано (действует тип-wide база), иначе effect.
type CellEffect = ResourcePermissionEffect | null;

function roleSortKey(role: RoleName): string {
  const idx = SYSTEM_ROLE_ORDER.indexOf(role);
  return idx >= 0 ? `0${idx}` : `1${role}`;
}

export interface ResourceTargetOption {
  id: string;
  label: string;
}

interface Props {
  resourceType: ResourceAclType;
  resourceId: string;
  /** Человекочитаемая подпись ресурса для заголовков и propagate-модалки. */
  resourceLabel?: string;
  /** Отдел ресурса — для подгрузки кастомных ролей. */
  departmentId: string | null;
  /** Можно ли менять гранты (RBAC родителя). */
  canEdit: boolean;
  /**
   * Загрузка однотипных ресурсов-целей для propagate. Сам образец можно не
   * исключать — компонент уберёт его из списка по `resourceId`.
   */
  fetchTargets: () => Promise<ResourceTargetOption[]>;
}

export function ResourceInstancePermissions({
  resourceType,
  resourceId,
  resourceLabel,
  departmentId,
  canEdit,
  fetchTargets,
}: Props) {
  const mockMode = useMockMode();
  const toast = useToast();
  const [refreshTick, setRefreshTick] = useState(0);
  const bump = useCallback(() => setRefreshTick((t) => t + 1), []);
  const [propagateOpen, setPropagateOpen] = useState(false);

  const catalogQ = useQuery(
    () =>
      mockMode
        ? Promise.resolve([MOCK_INSTANCE_CATALOG[resourceType]])
        : getPermissionCatalog(),
    [mockMode, resourceType],
  );

  const grantsQ = useQuery(
    () =>
      mockMode
        ? Promise.resolve({
            items: mockListResourcePermissions(resourceType, resourceId),
            total: 0,
          })
        : listResourcePermissions(resourceType, resourceId),
    [mockMode, resourceType, resourceId, refreshTick],
  );

  const rolesQ = useQuery(
    () =>
      mockMode || !departmentId
        ? Promise.resolve([] as ServiceRole[])
        : listServiceRoles(departmentId, "server_service"),
    [mockMode, departmentId, refreshTick],
  );

  const grants: ResourcePermissionEntry[] = useMemo(
    () => grantsQ.data?.items ?? [],
    [grantsQ.data],
  );
  const serviceRoles = useMemo(() => rolesQ.data ?? [], [rolesQ.data]);

  // Инстанс-грантуемые действия этого типа: из каталога убираем глобально-
  // только (create / callback'и воркера) и worker_only.
  const actions: PermissionCatalogAction[] = useMemo(() => {
    const entity = (catalogQ.data ?? []).find(
      (e) => e.entity_type === resourceType,
    );
    if (!entity) return [];
    return entity.actions.filter(
      (a) => isInstanceGrantable(a.action) && !a.worker_only,
    );
  }, [catalogQ.data, resourceType]);

  // Индекс грантов по `${role}::${action}` → effect.
  const grantIndex = useMemo(() => {
    const m = new Map<string, ResourcePermissionEffect>();
    for (const g of grants) m.set(`${g.role}::${g.action}`, g.effect);
    return m;
  }, [grants]);

  // Оптимистичный слой: ключ → effect (allow/deny) | null (снято, по базе).
  const [optimistic, setOptimistic] = useState<Record<string, CellEffect>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  const effectFor = useCallback(
    (role: RoleName, action: ActionName): CellEffect => {
      const key = `${role}::${action}`;
      if (key in optimistic) return optimistic[key];
      return grantIndex.get(key) ?? null;
    },
    [optimistic, grantIndex],
  );

  // Роли таблицы: системные + те, что уже есть в грантах + кастомные отдела.
  const roles = useMemo(() => {
    const set = new Set<RoleName>(SYSTEM_ROLE_ORDER);
    for (const g of grants) set.add(g.role);
    for (const r of serviceRoles) set.add(r.role_name);
    return Array.from(set).sort((a, b) =>
      roleSortKey(a).localeCompare(roleSortKey(b)),
    );
  }, [grants, serviceRoles]);

  // Цикл по клику: не задано → allow → deny → не задано.
  function nextEffect(current: CellEffect): CellEffect {
    if (current === null) return "allow";
    if (current === "allow") return "deny";
    return null;
  }

  const cycle = useCallback(
    async (role: RoleName, action: PermissionCatalogAction) => {
      if (!canEdit) return;
      if (role in LOCKED_ROLES) return;
      const key = `${role}::${action.action}`;
      if (saving[key]) return;
      const next = nextEffect(effectFor(role, action.action));
      setOptimistic((m) => ({ ...m, [key]: next }));
      setSaving((m) => ({ ...m, [key]: true }));
      try {
        if (next === null) {
          if (mockMode) {
            mockRevoke(resourceType, resourceId, role, action.action);
          } else {
            await revokeResourcePermission(
              resourceType,
              resourceId,
              role,
              action.action,
            );
          }
          toast.success(`Сброшено к базе: ${role}/${action.action}`);
        } else {
          if (mockMode) {
            mockGrant(resourceType, resourceId, role, action.action, next);
          } else {
            await grantResourcePermission(
              resourceType,
              resourceId,
              role,
              action.action,
              next,
            );
          }
          if (next === "deny") {
            toast.warn(`Запрет: ${role}/${action.action} (перекрывает базу)`);
          } else {
            toast[action.sensitive ? "warn" : "success"](
              action.sensitive
                ? `${role}/${action.action} выдано · sensitive → CRITICAL audit`
                : `Выдано: ${role}/${action.action}`,
            );
          }
        }
        setOptimistic((m) => {
          const { [key]: _drop, ...rest } = m;
          return rest;
        });
        bump();
      } catch (e) {
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
    [bump, canEdit, effectFor, mockMode, resourceId, resourceType, saving, toast],
  );

  if (catalogQ.loading || grantsQ.loading) {
    return (
      <div className="card flex items-center justify-center py-8">
        <div className="spinner">Загрузка инстанс-прав…</div>
      </div>
    );
  }

  if (catalogQ.error || grantsQ.error) {
    const err = catalogQ.error ?? grantsQ.error;
    return (
      <div className="card">
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
    <div className="card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-accent" />
          Инстанс-гранты {resourceType === "server" ? "сервера" : "учётки"}
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-dim">{grants.length} грант-строк</span>
          {canEdit && (
            <button
              className="btn btn-sm flex items-center gap-1"
              onClick={() => setPropagateOpen(true)}
            >
              <Share2 className="w-3.5 h-3.5" /> Распространить права
            </button>
          )}
        </div>
      </div>

      <p className="text-xs text-dim leading-relaxed mb-2">
        Точечные права на{" "}
        <span className="mono">{resourceLabel ?? resourceId}</span> поверх
        глобальной (тип-wide) матрицы. Клик по ячейке прокручивает три состояния:
        пусто → разрешить → запретить → пусто. Глобальные действия (
        <span className="mono">create</span> и пр.) настраиваются в админ-разделе
        «Права server_service».
      </p>
      <div className="text-[11px] text-dim flex items-center gap-4 flex-wrap mb-3">
        <span className="flex items-center gap-1">
          <Minus className="w-3 h-3 text-dim" /> по базе (тип-wide)
        </span>
        <span className="flex items-center gap-1">
          <Check className="w-3 h-3 text-emerald-500" /> allow — добавить
        </span>
        <span className="flex items-center gap-1">
          <Ban className="w-3 h-3 text-danger" /> deny — перекрыть базу (запрет)
        </span>
        <span className="flex items-center gap-1">
          <Lock className="w-3 h-3" /> системная роль — фиксирована
        </span>
      </div>

      {actions.length === 0 ? (
        <div className="text-sm text-dim">
          Нет инстанс-грантуемых действий в каталоге.
        </div>
      ) : (
        <div className="overflow-auto max-h-[60vh] border border-token rounded">
          <table className="text-sm border-collapse">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pt-2 px-3 sticky left-0 top-0 z-20 bg-[var(--bg-soft)]">
                  role
                </th>
                {actions.map((a) => (
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
                    {actions.map((a) => {
                      const key = `${role}::${a.action}`;
                      const effect = effectFor(role, a.action);
                      const isSaving = !!saving[key];
                      const disabled = !canEdit || locked;
                      const reason = locked
                        ? "системная роль — фиксирована"
                        : !canEdit
                          ? "Нет прав на изменение"
                          : "Клик: пусто → allow → deny → пусто";
                      return (
                        <td key={a.action} className="py-1.5 px-2 text-center">
                          <PermEffectCell
                            effect={effect}
                            disabled={disabled}
                            saving={isSaving}
                            title={`${a.description}\n\n${reason}`}
                            onClick={() => cycle(role, a)}
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

      {!canEdit && (
        <div className="mt-3 text-[11px] text-dim flex items-center gap-1">
          <Lock className="w-3 h-3" /> просмотр без права изменения
        </div>
      )}

      {propagateOpen && (
        <PropagateModal
          resourceType={resourceType}
          sourceId={resourceId}
          sourceLabel={resourceLabel ?? resourceId}
          grantCount={grants.length}
          mockMode={mockMode}
          fetchTargets={fetchTargets}
          onClose={() => setPropagateOpen(false)}
          onDone={bump}
        />
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Propagate modal
// ───────────────────────────────────────────────────────────────────────────

function PropagateModal({
  resourceType,
  sourceId,
  sourceLabel,
  grantCount,
  mockMode,
  fetchTargets,
  onClose,
  onDone,
}: {
  resourceType: ResourceAclType;
  sourceId: string;
  sourceLabel: string;
  grantCount: number;
  mockMode: boolean;
  fetchTargets: () => Promise<ResourceTargetOption[]>;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [mode, setMode] = useState<ResourcePropagateMode>("merge");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ResourcePropagateResponse | null>(null);

  const targetsQ = useQuery(() => fetchTargets(), []);
  const targets = useMemo(
    () => (targetsQ.data ?? []).filter((t) => t.id !== sourceId),
    [targetsQ.data, sourceId],
  );

  function toggleTarget(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function run() {
    if (pending || selected.size === 0) return;
    setPending(true);
    setResult(null);
    const ids = Array.from(selected);
    try {
      const res = mockMode
        ? mockPropagate(resourceType, sourceId, ids, mode)
        : await propagateResourcePermissions(resourceType, sourceId, {
            target_resource_ids: ids,
            mode,
          });
      setResult(res);
      const added = res.targets.reduce((s, t) => s + t.added, 0);
      const removed = res.targets.reduce((s, t) => s + t.removed, 0);
      toast.success(
        `Распространено на ${res.targets.length} цел. · +${added}${
          mode === "mirror" ? ` / −${removed}` : ""
        }`,
      );
      onDone();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось распространить права"));
    } finally {
      setPending(false);
    }
  }

  const labelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const t of targets) m.set(t.id, t.label);
    return m;
  }, [targets]);

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !pending && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <Share2 className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Распространить инстанс-права
            </Dialog.Title>
          </div>

          <div className="modal-body">
            <Dialog.Description className="text-sm text-dim mb-3">
              Скопировать {grantCount} инстанс-грант(ов) с{" "}
              <span className="mono">{sourceLabel}</span> на выбранные
              {resourceType === "server" ? " серверы" : " учётки"} того же типа.
            </Dialog.Description>

            {/* ── Режим ── */}
            <div className="mb-4">
              <label className="field-label">Режим</label>
              <div className="flex flex-col gap-2">
                <label className="flex items-start gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name="propagate-mode"
                    checked={mode === "merge"}
                    onChange={() => setMode("merge")}
                    disabled={pending}
                  />
                  <span>
                    <span className="font-medium">merge</span>
                    <span className="text-dim">
                      {" "}
                      — добавить недостающее, лишние гранты целей оставить.
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name="propagate-mode"
                    checked={mode === "mirror"}
                    onChange={() => setMode("mirror")}
                    disabled={pending}
                  />
                  <span>
                    <span className="font-medium">mirror</span>
                    <span className="text-dim">
                      {" "}
                      — точная копия образца (добавить недостающее + удалить
                      лишнее).
                    </span>
                  </span>
                </label>
              </div>
            </div>

            {/* ── Цели ── */}
            <div>
              <label className="field-label flex items-center justify-between">
                <span>Цели · выбрано {selected.size}</span>
                {targets.length > 0 && (
                  <button
                    className="btn btn-sm"
                    type="button"
                    disabled={pending}
                    onClick={() =>
                      setSelected((prev) =>
                        prev.size === targets.length
                          ? new Set()
                          : new Set(targets.map((t) => t.id)),
                      )
                    }
                  >
                    {selected.size === targets.length
                      ? "Снять все"
                      : "Выбрать все"}
                  </button>
                )}
              </label>
              {targetsQ.loading && (
                <div className="text-xs text-dim py-2">Загрузка…</div>
              )}
              {targetsQ.error && (
                <div className="alert-danger text-xs">
                  {apiErrMsg(targetsQ.error, "Цели не загрузились")}
                  <button
                    className="btn btn-sm ml-2"
                    onClick={() => targetsQ.refetch()}
                  >
                    Повторить
                  </button>
                </div>
              )}
              {!targetsQ.loading && !targetsQ.error && targets.length === 0 && (
                <div className="text-xs text-dim py-2">
                  Других ресурсов того же типа нет.
                </div>
              )}
              {targets.length > 0 && (
                <div className="flex flex-col gap-0.5 max-h-64 overflow-y-auto mt-1">
                  {targets.map((t) => (
                    <label
                      key={t.id}
                      className="cred-row flex items-center gap-2 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(t.id)}
                        disabled={pending}
                        onChange={() => toggleTarget(t.id)}
                      />
                      <span className="text-sm truncate mono flex-1 min-w-0">
                        {t.label}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* ── Результат ── */}
            {result && (
              <div className="border-t border-token mt-4 pt-3">
                <div className="text-[11px] uppercase text-dim mb-2">
                  Результат · образец {result.source_grant_count} грант(ов)
                </div>
                <div className="flex flex-col gap-0.5 max-h-48 overflow-y-auto">
                  {result.targets.map((t) => (
                    <div
                      key={t.resource_id}
                      className="flex items-center gap-2 text-xs"
                    >
                      <span className="mono flex-1 min-w-0 truncate">
                        {labelById.get(t.resource_id) ?? t.resource_id}
                      </span>
                      <span className="badge badge-ok">+{t.added}</span>
                      {result.mode === "mirror" && (
                        <span className="badge badge-danger">−{t.removed}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="modal-footer flex justify-end gap-2">
            <button className="btn" onClick={onClose} disabled={pending}>
              {result ? "Закрыть" : "Отмена"}
            </button>
            <button
              className="btn btn-primary flex items-center gap-1"
              onClick={run}
              disabled={pending || selected.size === 0}
            >
              {pending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Распространить
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Tri-state cell: пусто (по базе) / allow / deny
// ───────────────────────────────────────────────────────────────────────────

function PermEffectCell({
  effect,
  disabled,
  saving,
  title,
  onClick,
}: {
  effect: CellEffect;
  disabled: boolean;
  saving: boolean;
  title: string;
  onClick: () => void;
}) {
  if (saving) {
    return (
      <span
        className="inline-flex items-center justify-center w-6 h-6 align-middle"
        title={title}
      >
        <Loader2 className="w-3.5 h-3.5 animate-spin text-dim" />
      </span>
    );
  }
  const icon =
    effect === "allow" ? (
      <Check className="w-4 h-4 text-emerald-500" />
    ) : effect === "deny" ? (
      <Ban className="w-4 h-4 text-danger" />
    ) : (
      <Minus className="w-4 h-4 text-dim opacity-50" />
    );
  const label =
    effect === "allow" ? "allow" : effect === "deny" ? "deny" : "по базе";
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={effect === "allow"}
      aria-label={label}
      className={[
        "inline-flex items-center justify-center w-6 h-6 rounded align-middle",
        disabled
          ? "opacity-50 cursor-not-allowed"
          : "cursor-pointer hover:bg-[var(--bg-soft)]",
      ].join(" ")}
      disabled={disabled}
      title={title}
      onClick={onClick}
    >
      {icon}
    </button>
  );
}
