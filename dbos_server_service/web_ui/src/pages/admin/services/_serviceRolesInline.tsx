/**
 * Inline CRUD widget for service roles, scoped to a single
 * `(department_id, service_name)` pair.
 *
 * Used from `ServicesCatalog` (роли выбранного отдела внутри карточки сервиса)
 * и `ServicesDepartments` (роли по каждому granted-сервису внутри карточки
 * отдела). Самостоятельная полная страница «Роли · <service>» — это
 * `ServiceRolesCard.tsx`, она остаётся в `/admin/services.<svc>.roles` и
 * этим виджетом не заменяется.
 *
 * Бэкенд:
 *   GET    /departments/{dept_id}/services/{svc}/roles
 *   POST   /departments/{dept_id}/services/{svc}/roles          {role_name, description?}
 *   PATCH  /departments/{dept_id}/services/{svc}/roles/{name}   {description?}
 *   DELETE /departments/{dept_id}/services/{svc}/roles/{name}
 *
 * Системные роли (`is_system=true`) — кнопки edit/delete показываем как
 * disabled с пояснением (бэкенд вернёт SERVICE_ROLE_SYSTEM_LOCKED).
 */

import { useState } from "react";
import { Loader2, Pencil, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import {
  createServiceRole,
  deleteServiceRole,
  listServiceRoles,
  patchServiceRole,
} from "@/api/auth/service_roles";
import type { ServiceName, ServiceRole } from "@/api/auth/types";
import { useToast } from "@/contexts/ToastContext";

type Mode = "list" | "create" | { kind: "edit"; role: ServiceRole };

export function ServiceRolesInline({
  departmentId,
  serviceName,
  canEdit,
  compact = false,
}: {
  departmentId: string;
  serviceName: ServiceName;
  /** Платформенный admin или dep_admin своего отдела. */
  canEdit: boolean;
  /** Версия для вложения в карточку отдела — без заголовка/подсказки сверху. */
  compact?: boolean;
}) {
  const rolesQ = useQuery<ServiceRole[]>(
    () => listServiceRoles(departmentId, serviceName),
    [departmentId, serviceName],
  );
  const [mode, setMode] = useState<Mode>("list");

  function back() {
    setMode("list");
  }

  function onSaved() {
    rolesQ.refetch();
    setMode("list");
  }

  const roles = rolesQ.data ?? [];

  return (
    <div className={compact ? "" : "mt-4 pt-3 border-t border-token"}>
      {!compact && (
        <div className="text-sm font-semibold mb-2 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-accent" />
          Роли сервиса в отделе
        </div>
      )}

      {rolesQ.error && (
        <div className="alert-danger text-[11px] mb-2">
          {rolesQ.error instanceof ApiError
            ? `${rolesQ.error.errorCode}: ${rolesQ.error.message}`
            : rolesQ.error.message}
        </div>
      )}

      {mode === "list" && (
        <>
          {rolesQ.loading ? (
            <div className="text-[11px] text-dim flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> Загрузка ролей…
            </div>
          ) : roles.length === 0 ? (
            <div className="text-[11px] text-dim italic mb-2">
              В scope ({departmentId}, {serviceName}) ролей нет.
            </div>
          ) : (
            <div className="flex flex-col gap-1 mb-2">
              {roles.map((r) => (
                <RoleRow
                  key={r.role_name}
                  role={r}
                  canEdit={canEdit}
                  onEdit={() => setMode({ kind: "edit", role: r })}
                  onDeleted={() => rolesQ.refetch()}
                  departmentId={departmentId}
                  serviceName={serviceName}
                />
              ))}
            </div>
          )}
          {canEdit && (
            <button
              className="btn btn-ghost text-xs flex items-center gap-1"
              onClick={() => setMode("create")}
            >
              <Plus className="w-3 h-3" /> Создать роль
            </button>
          )}
        </>
      )}

      {mode === "create" && (
        <RoleForm
          departmentId={departmentId}
          serviceName={serviceName}
          mode="new"
          onDone={onSaved}
          onCancel={back}
        />
      )}

      {typeof mode === "object" && mode.kind === "edit" && (
        <RoleForm
          departmentId={departmentId}
          serviceName={serviceName}
          mode="edit"
          initial={mode.role}
          onDone={onSaved}
          onCancel={back}
        />
      )}
    </div>
  );
}

function RoleRow({
  role,
  canEdit,
  onEdit,
  onDeleted,
  departmentId,
  serviceName,
}: {
  role: ServiceRole;
  canEdit: boolean;
  onEdit: () => void;
  onDeleted: () => void;
  departmentId: string;
  serviceName: ServiceName;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const locked = role.is_system;

  async function onDelete() {
    if (!window.confirm(`Удалить роль ${role.role_name}?`)) return;
    setBusy(true);
    try {
      await deleteServiceRole(departmentId, serviceName, role.role_name);
      toast.success(`Роль ${role.role_name} удалена`);
      onDeleted();
    } catch (e) {
      const msg =
        e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2 text-sm py-1 border-b border-dashed border-token last:border-b-0">
      <span className="mono truncate">{role.role_name}</span>
      {role.description && (
        <span className="text-dim text-[11px] truncate flex-1">
          {role.description}
        </span>
      )}
      {role.is_system ? (
        <span className="badge">system</span>
      ) : (
        <span className="badge badge-accent">custom</span>
      )}
      {canEdit && (
        <div className="flex items-center gap-1">
          <button
            className="btn btn-ghost text-xs flex items-center gap-1"
            disabled={busy || locked}
            title={locked ? "Системную роль править нельзя" : undefined}
            onClick={onEdit}
          >
            <Pencil className="w-3 h-3" />
          </button>
          <button
            className="btn btn-ghost text-xs flex items-center gap-1"
            disabled={busy || locked}
            title={locked ? "Системную роль удалить нельзя" : undefined}
            onClick={onDelete}
          >
            {busy ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Trash2 className="w-3 h-3" />
            )}
          </button>
        </div>
      )}
    </div>
  );
}

function RoleForm({
  departmentId,
  serviceName,
  mode,
  initial,
  onDone,
  onCancel,
}: {
  departmentId: string;
  serviceName: ServiceName;
  mode: "new" | "edit";
  initial?: ServiceRole;
  onDone: () => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const [roleName, setRoleName] = useState(initial?.role_name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (mode === "new" && !roleName.trim()) {
      toast.warn("role_name обязателен");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (mode === "new") {
        await createServiceRole(departmentId, serviceName, {
          role_name: roleName.trim(),
          description: description.trim() || undefined,
        });
        toast.success("Роль создана");
      } else if (initial) {
        const body: { description?: string } = {};
        if ((description ?? "") !== (initial.description ?? "")) {
          body.description = description.trim();
        }
        await patchServiceRole(
          departmentId,
          serviceName,
          initial.role_name,
          body,
        );
        toast.success("Роль обновлена");
      }
      onDone();
    } catch (e) {
      const msg =
        e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 p-3 border border-token rounded">
      <div className="text-sm font-semibold mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-accent" />
          {mode === "new"
            ? `Новая роль · ${serviceName}`
            : `Edit · ${initial?.role_name}`}
        </span>
        <button
          className="btn btn-ghost text-xs flex items-center gap-1"
          onClick={onCancel}
          disabled={busy}
        >
          <X className="w-3 h-3" /> закрыть
        </button>
      </div>
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-dim">role_name</span>
          <input
            className="input mono"
            value={roleName}
            onChange={(e) => setRoleName(e.target.value)}
            placeholder="my_role"
            disabled={mode === "edit"}
          />
          {mode === "edit" && (
            <span className="text-[10px] text-dim">
              role_name неизменяем после создания
            </span>
          )}
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-dim">description</span>
          <textarea
            className="input"
            value={description ?? ""}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
      </div>
      {err && <div className="alert-danger mt-2 text-[11px]">{err}</div>}
      <div className="mt-3 flex gap-2 justify-end">
        <button className="btn" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        <button
          className="btn btn-primary flex items-center gap-1"
          onClick={submit}
          disabled={
            busy ||
            (mode === "new" && !roleName.trim())
          }
        >
          {busy && <Loader2 className="w-3 h-3 animate-spin" />}
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
