/**
 * Инстанс-уровневый ACL ресурса (`server` / `server_account`).
 *
 * Каждая ячейка — чекбокс, проставленный по базовой (тип-wide) матрице
 * `entity_permissions`. Снятая с базово-разрешённого галка пишет инстанс-`deny`,
 * поставленная сверх базы — `allow`, возврат к базовому значению снимает
 * инстанс-override. Правки копятся локально и применяются батчем по кнопке
 * «Сохранить» (PUT allow/deny / DELETE по изменённым ячейкам). Глобальные
 * действия (`create` и callback'и воркера) сюда не попадают.
 *
 * Системные роли `admin`/`guest` показаны, но залочены — их ячейки отражают
 * реальные базовые права (admin — всё, guest — view). Внутренняя `worker_bot`
 * в матрицу не выводится. Если у роли есть любое право на ресурсе, `view`
 * проставляется и блокируется автоматически.
 *
 * Кнопка «Распространить права» копирует инстанс-гранты этого ресурса на
 * выбранные однотипные цели (merge — добавить недостающее; mirror — привести к
 * точной копии). Источник цели-списка — `fetchTargets` от родителя.
 *
 * Backend: server_service/src/api/v1/endpoints/resource_permissions.py.
 */
import { useCallback, useMemo, useState } from "react";
import {
  Loader2,
  Lock,
  RotateCcw,
  Save,
  Share2,
  ShieldCheck,
} from "lucide-react";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { getPermissionCatalog, listPermissions } from "@/api/server/permissions";
import {
  grantResourcePermission,
  listResourcePermissions,
  propagateResourcePermissions,
  revokeResourcePermission,
} from "@/api/server/resourcePermissions";
import { listServiceRoles } from "@/api/auth/service_roles";
import {
  MOCK_INSTANCE_CATALOG,
  mockBaseMatrix,
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
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";

// Системные роли в фиксированном порядке; кастомные идут после по алфавиту.
const SYSTEM_ROLE_ORDER: RoleName[] = ["guest", "admin"];

// Внутренние роли, которые в инстанс-матрице не показываем вообще (гранты на
// backend у них остаются — это чисто UI-скрытие).
const HIDDEN_ROLES: ReadonlySet<RoleName> = new Set<RoleName>(["worker_bot"]);

// Роли, чьи права фиксированы платформой — их нельзя трогать ни тут, ни на
// backend (вернёт 409 SYSTEM_ROLE_IMMUTABLE). Контролы для них read-only,
// чекбоксы отражают реальную базу (admin — всё, guest — view).
const LOCKED_ROLES: Record<string, string> = {
  admin: "admin держит полный доступ — правила не редактируются",
  guest: "guest — базовая роль, правила не редактируются",
};

// Инстанс-override ячейки: null — нет override (действует тип-wide база).
type CellEffect = ResourcePermissionEffect | null;

// Запасные русские подписи действий — на случай, если каталог отдал пустое или
// неудобное описание. Берётся только когда `description` пуст.
const ACTION_LABELS_RU: Partial<Record<ActionName, string>> = {
  view: "Просмотр карточки",
  update: "Редактирование",
  delete: "Удаление",
  busy_acquire: "Захват в работу",
  busy_release: "Снятие брони",
  os_sync: "Смена версии ОС вручную",
  power_on: "Включение питания (BMC)",
  power_off: "Выключение питания (BMC)",
  power_reboot: "Перезагрузка (BMC)",
  power_status: "Опрос состояния питания",
  inventory_trigger: "Запуск инвентаризации",
  view_drift: "Просмотр дрейфа конфигурации",
  view_password: "Просмотр пароля",
  rotate_password: "Ротация пароля",
  grant_sudo: "Выдача sudo",
  view_credentials: "Просмотр учётных данных",
  rotate_credentials: "Ротация учётных данных",
  cancel: "Отмена задачи",
};

// Гарантированно непустое русское описание действия для тултипов.
function actionTitle(a: PermissionCatalogAction): string {
  const desc = a.description?.trim();
  if (desc) return desc;
  return ACTION_LABELS_RU[a.action] ?? a.action;
}

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

  // Базовая (тип-wide) матрица — тот же источник, что у админ-страницы прав.
  const baseQ = useQuery(
    () =>
      mockMode
        ? Promise.resolve(mockBaseMatrix(resourceType))
        : listPermissions({}),
    [mockMode, resourceType, refreshTick],
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

  // Базовая матрица как множество `${role}::${action}` по нужному типу.
  const baseSet = useMemo(() => {
    const s = new Set<string>();
    for (const p of baseQ.data?.items ?? []) {
      if (p.entity_type === resourceType) s.add(`${p.role}::${p.action}`);
    }
    return s;
  }, [baseQ.data, resourceType]);

  // Даёт ли роли это действие тип-wide база. admin — полный доступ всегда.
  const baseAllowed = useCallback(
    (role: RoleName, action: ActionName): boolean => {
      if (role === "admin") return true;
      return baseSet.has(`${role}::${action}`);
    },
    [baseSet],
  );

  // Инстанс-override роли по серверу (без локальных правок).
  const grantIndex = useMemo(() => {
    const m = new Map<string, ResourcePermissionEffect>();
    for (const g of grants) m.set(`${g.role}::${g.action}`, g.effect);
    return m;
  }, [grants]);

  // Локальные правки: ключ → желаемый override (allow/deny/null). Применяются
  // батчем по «Сохранить»; ключ присутствует только у тронутой ячейки.
  const [edits, setEdits] = useState<Record<string, CellEffect>>({});
  const [saving, setSaving] = useState(false);

  const serverOverride = useCallback(
    (key: string): CellEffect => grantIndex.get(key) ?? null,
    [grantIndex],
  );

  // Желаемый override ячейки (правка важнее серверного состояния).
  const currentOverride = useCallback(
    (role: RoleName, action: ActionName): CellEffect => {
      const key = `${role}::${action}`;
      return key in edits ? edits[key] : serverOverride(key);
    },
    [edits, serverOverride],
  );

  // Состояние галки без учёта авто-view: override поверх базы.
  const rawChecked = useCallback(
    (role: RoleName, action: ActionName): boolean => {
      const ov = currentOverride(role, action);
      if (ov === "allow") return true;
      if (ov === "deny") return false;
      return baseAllowed(role, action);
    },
    [currentOverride, baseAllowed],
  );

  // Есть ли у роли любое НЕ-view право на ресурсе — тогда view авто-включён.
  const viewForced = useCallback(
    (role: RoleName): boolean =>
      actions.some((a) => a.action !== "view" && rawChecked(role, a.action)),
    [actions, rawChecked],
  );

  const effectiveChecked = useCallback(
    (role: RoleName, action: ActionName): boolean => {
      if (action === "view" && viewForced(role)) return true;
      return rawChecked(role, action);
    },
    [rawChecked, viewForced],
  );

  // Роли таблицы: системные + из грантов + кастомные отдела, без скрытых.
  const roles = useMemo(() => {
    const set = new Set<RoleName>(SYSTEM_ROLE_ORDER);
    for (const g of grants) set.add(g.role);
    for (const r of serviceRoles) set.add(r.role_name);
    for (const h of HIDDEN_ROLES) set.delete(h);
    return Array.from(set).sort((a, b) =>
      roleSortKey(a).localeCompare(roleSortKey(b)),
    );
  }, [grants, serviceRoles]);

  // Кастомные роли — всё, что не системное (для точечной правки и очистки).
  const customRoles = useMemo(
    () => roles.filter((r) => !SYSTEM_ROLE_ORDER.includes(r)),
    [roles],
  );

  // Ключи, чьё желаемое состояние расходится с серверным — это и есть дифф.
  const diffKeys = useMemo(
    () => Object.keys(edits).filter((k) => edits[k] !== serverOverride(k)),
    [edits, serverOverride],
  );
  const dirty = diffKeys.length > 0;

  // Есть ли хоть один инстанс-override (с учётом несохранённых правок) — для
  // бейджа «кастомные права» у ресурса.
  const hasOverrides = useMemo(() => {
    for (const role of roles) {
      for (const a of actions) {
        if (currentOverride(role, a.action) !== null) return true;
      }
    }
    return false;
  }, [roles, actions, currentOverride]);

  const toggle = useCallback(
    (role: RoleName, action: ActionName) => {
      if (!canEdit || role in LOCKED_ROLES) return;
      if (action === "view" && viewForced(role)) return;
      const key = `${role}::${action}`;
      const base = baseAllowed(role, action);
      const desired = !effectiveChecked(role, action);
      const override: CellEffect =
        desired === base ? null : desired ? "allow" : "deny";
      setEdits((m) => ({ ...m, [key]: override }));
    },
    [canEdit, viewForced, baseAllowed, effectiveChecked],
  );

  // Снять все инстанс-override роли — вернуть её ячейки к базе.
  const clearRole = useCallback(
    (role: RoleName) => {
      if (!canEdit || role in LOCKED_ROLES) return;
      setEdits((m) => {
        const next = { ...m };
        for (const a of actions) next[`${role}::${a.action}`] = null;
        return next;
      });
    },
    [canEdit, actions],
  );

  // Снять все инстанс-override на ресурсе (по всем кастомным ролям).
  const clearAll = useCallback(() => {
    if (!canEdit) return;
    setEdits((m) => {
      const next = { ...m };
      for (const role of customRoles) {
        for (const a of actions) next[`${role}::${a.action}`] = null;
      }
      return next;
    });
  }, [canEdit, customRoles, actions]);

  const resetEdits = useCallback(() => setEdits({}), []);

  // Применить дифф батчем: PUT для allow/deny, DELETE для возврата к базе.
  const save = useCallback(async () => {
    if (!canEdit || saving || diffKeys.length === 0) return;
    setSaving(true);
    const done: string[] = [];
    let ok = 0;
    let failed = 0;
    for (const key of diffKeys) {
      const [role, action] = key.split("::") as [RoleName, ActionName];
      const target = edits[key];
      try {
        if (target === null) {
          if (mockMode) mockRevoke(resourceType, resourceId, role, action);
          else
            await revokeResourcePermission(
              resourceType,
              resourceId,
              role,
              action,
            );
        } else {
          if (mockMode) mockGrant(resourceType, resourceId, role, action, target);
          else
            await grantResourcePermission(
              resourceType,
              resourceId,
              role,
              action,
              target,
            );
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
  }, [
    canEdit,
    saving,
    diffKeys,
    edits,
    mockMode,
    resourceType,
    resourceId,
    toast,
    bump,
  ]);

  if (catalogQ.loading || grantsQ.loading || baseQ.loading) {
    return (
      <div className="card flex items-center justify-center py-8">
        <div className="spinner">Загрузка инстанс-прав…</div>
      </div>
    );
  }

  if (catalogQ.error || grantsQ.error || baseQ.error) {
    const err = catalogQ.error ?? grantsQ.error ?? baseQ.error;
    return (
      <div className="card">
        <div className="alert-danger flex items-center gap-2">
          <span>{apiErrMsg(err)}</span>
          <Button size="sm"
            onClick={() => {
              catalogQ.refetch();
              grantsQ.refetch();
              baseQ.refetch();
            }}
          >
            Повторить
          </Button>
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
          {hasOverrides && (
            <Badge kind="warn"
              className="text-[10px]"
              title="на ресурсе есть точечные права поверх базовой матрицы"
            >
              кастомные права
            </Badge>
          )}
        </h3>
        {canEdit && (
          <Button size="sm"
            className="flex items-center gap-1"
            onClick={() => setPropagateOpen(true)}
          >
            <Share2 className="w-3.5 h-3.5" /> Распространить права
          </Button>
        )}
      </div>

      <p className="text-xs text-dim leading-relaxed mb-2">
        Точечные права на{" "}
        <span className="mono">{resourceLabel ?? resourceId}</span> поверх
        глобальной (тип-wide) матрицы. Галки проставлены по базовой матрице:
        снимите галку с базово-разрешённого — будет <span className="mono">deny</span>,
        поставьте сверх базы — <span className="mono">allow</span>. Глобальные
        действия (<span className="mono">create</span> и пр.) настраиваются в
        админ-разделе «Права server_service».
      </p>
      <div className="text-[11px] text-dim flex items-center gap-4 flex-wrap mb-3">
        <span className="flex items-center gap-1">
          <Lock className="w-3 h-3" /> системная роль — фиксирована
        </span>
        <span>view включается автоматически у роли с другими правами</span>
      </div>

      {actions.length === 0 ? (
        <div className="text-sm text-dim">
          Нет инстанс-грантуемых действий в каталоге.
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
                  {actions.map((a) => (
                    <th
                      key={a.action}
                      className="pb-2 pt-2 px-2 mono font-normal align-bottom sticky top-0 z-10 bg-[var(--bg-soft)]"
                      title={actionTitle(a)}
                    >
                      <div className="flex items-center gap-1 whitespace-nowrap">
                        <span>{a.action}</span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {roles.map((role) => {
                  const locked = role in LOCKED_ROLES;
                  const roleDirty = actions.some(
                    (a) => currentOverride(role, a.action) !== null,
                  );
                  return (
                    <tr key={role} className="border-t border-token">
                      <td className="py-2 px-3 mono text-xs sticky left-0 z-10 bg-[var(--bg-soft)]">
                        <span
                          className="flex items-center gap-1"
                          title={locked ? LOCKED_ROLES[role] : undefined}
                        >
                          {locked && <Lock className="w-3 h-3 text-dim" />}
                          {role}
                          {!locked && canEdit && (
                            <Button variant="ghost"
                              type="button"
                              className="p-0.5 disabled:opacity-30"
                              title="Очистить права роли в таблице — вернуть к базе"
                              aria-label={`Очистить роль ${role}`}
                              disabled={saving || !roleDirty}
                              onClick={() => clearRole(role)}
                            >
                              <RotateCcw className="w-3 h-3 text-dim" />
                            </Button>
                          )}
                        </span>
                      </td>
                      {actions.map((a) => {
                        const checked = effectiveChecked(role, a.action);
                        const autoView =
                          a.action === "view" && viewForced(role);
                        const disabled =
                          !canEdit || locked || autoView || saving;
                        const reason = locked
                          ? "системная роль — фиксирована"
                          : autoView
                            ? "view включён автоматически, т.к. у роли есть другие права; снимите их, чтобы менять view"
                            : !canEdit
                              ? "Нет прав на изменение"
                              : currentOverride(role, a.action) === null
                                ? "по базовой матрице"
                                : currentOverride(role, a.action) === "allow"
                                  ? "allow — выдано сверх базы"
                                  : "deny — снято с базы";
                        return (
                          <td
                            key={a.action}
                            className="py-1.5 px-2 text-center"
                          >
                            <PermCheckbox
                              checked={checked}
                              disabled={disabled}
                              title={`${actionTitle(a)}\n\n${reason}`}
                              onChange={() => toggle(role, a.action)}
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

          {canEdit && (
            <div className="sticky bottom-0 z-30 mt-3 -mb-1 flex items-center gap-2 flex-wrap border-t border-token bg-[var(--bg-soft)] pt-3 pb-2">
              <Button variant="primary" size="sm"
                className="flex items-center gap-1"
                onClick={save}
                disabled={!dirty || saving}
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
                Сохранить
                {dirty && ` (${diffKeys.length})`}
              </Button>
              <Button size="sm"
                onClick={resetEdits}
                disabled={!dirty || saving}
              >
                Отмена
              </Button>
              <Button size="sm"
                className="flex items-center gap-1"
                onClick={clearAll}
                disabled={saving || !hasOverrides}
                title="Снять все инстанс-override на этом ресурсе"
              >
                <RotateCcw className="w-3.5 h-3.5" /> Очистить все роли
              </Button>
              <span className="text-[11px] text-dim ml-auto">
                {dirty
                  ? `Несохранённых изменений: ${diffKeys.length}`
                  : "Изменения применяются по кнопке «Сохранить»"}
              </span>
            </div>
          )}
        </>
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
    <Modal
      open
      onOpenChange={(o) => !o && !pending && onClose()}
      title="Распространить инстанс-права"
      icon={<Share2 className="w-5 h-5 text-accent" />}
      hideCloseButton
    >
          <div className="modal-body">
            <p className="text-sm text-dim mb-3">
              Скопировать {grantCount} инстанс-грант(ов) с{" "}
              <span className="mono">{sourceLabel}</span> на выбранные
              {resourceType === "server" ? " серверы" : " учётки"} того же типа.
            </p>

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
                  <Button size="sm"
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
                  </Button>
                )}
              </label>
              {targetsQ.loading && (
                <div className="text-xs text-dim py-2">Загрузка…</div>
              )}
              {targetsQ.error && (
                <div className="alert-danger text-xs">
                  {apiErrMsg(targetsQ.error, "Цели не загрузились")}
                  <Button size="sm"
                    className="ml-2"
                    onClick={() => targetsQ.refetch()}
                  >
                    Повторить
                  </Button>
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
                      <Checkbox
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
                      <Badge kind="ok">+{t.added}</Badge>
                      {result.mode === "mirror" && (
                        <Badge kind="danger">−{t.removed}</Badge>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="modal-footer flex justify-end gap-2">
            <Button onClick={onClose} disabled={pending}>
              {result ? "Закрыть" : "Отмена"}
            </Button>
            <Button variant="primary"
              className="flex items-center gap-1"
              onClick={run}
              disabled={pending || selected.size === 0}
            >
              {pending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Распространить
            </Button>
          </div>
    </Modal>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Checkbox cell — вкл/выкл поверх базовой матрицы
// ───────────────────────────────────────────────────────────────────────────

function PermCheckbox({
  checked,
  disabled,
  title,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  title: string;
  onChange: () => void;
}) {
  return (
    <Checkbox
      className={[
        "w-4 h-4 align-middle accent-[var(--accent)]",
        disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
      ].join(" ")}
      checked={checked}
      disabled={disabled}
      title={title}
      onChange={onChange}
    />
  );
}
