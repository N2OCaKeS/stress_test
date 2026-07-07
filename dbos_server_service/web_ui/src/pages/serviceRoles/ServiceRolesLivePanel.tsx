/**
 * Live-data panel for service-roles editors.
 *
 * The four ServiceRoles*Admin pages (`AccountAdmin`, `DepAdmin`, `SecretAdmin`,
 * `MultiAdmin`) all render a mocked role catalogue. This panel sits next to
 * the mock view and pulls the *real* catalogue for the persona's dept-scope
 * + selected service via `src/api/auth/service_roles.ts`. Switching
 * service in the dropdown re-queries. Mutations (create / rename
 * description / delete custom role / bulk assign / bulk revoke) call the
 * real endpoints; on success we refetch.
 *
 * In mock mode the panel renders nothing — the page falls back to the
 * static mock catalogue.
 */

import { useCallback, useState } from "react";
import { ShieldCheck, Plus, Trash2, UserPlus, UserMinus } from "lucide-react";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import * as srApi from "@/api/auth/service_roles";
import { ApiError } from "@/api/client";
import { useDeptLabel } from "@/lib/labels";
import type { ServiceName } from "@/api/auth/types";

const DEFAULT_SERVICES: ServiceName[] = [
  "server_service",
  "secret_service",
  "loging_service",
  "config_service",
];

// Зеркало `auth_service/src/schemas/service_roles.py::ServiceRoleCreate.role_name`:
// lower-snake_case, начинается с буквы, 2..64 символа.
const ROLE_NAME_RE = /^[a-z][a-z0-9_]+$/;

function validateRoleName(value: string): string | null {
  if (value.length < 2) return "role_name: минимум 2 символа";
  if (value.length > 64) return "role_name: максимум 64 символа";
  if (!ROLE_NAME_RE.test(value)) {
    return "role_name: только строчные латиница/цифры/`_`, начинается с буквы";
  }
  return null;
}

export function ServiceRolesLivePanel({
  departmentId,
  initialService = "server_service",
  canCreate = true,
  scopeNote,
}: {
  departmentId: string;
  initialService?: ServiceName;
  canCreate?: boolean;
  scopeNote?: string;
}) {
  const mock = useMockMode();
  const deptLabel = useDeptLabel(departmentId);
  const [serviceName, setServiceName] = useState<ServiceName>(initialService);
  const [refreshTick, setRefreshTick] = useState(0);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Create-role form
  const [newRoleName, setNewRoleName] = useState("");
  const [newRoleDesc, setNewRoleDesc] = useState("");

  // Bulk-assign form (per role)
  const [bulkRole, setBulkRole] = useState("");
  const [bulkUserIds, setBulkUserIds] = useState("");

  const rolesQ = useQuery(
    () => srApi.listServiceRoles(departmentId, serviceName),
    [departmentId, serviceName, refreshTick],
    { enabled: !mock && !!departmentId },
  );

  const refetch = useCallback(
    () => setRefreshTick((t) => t + 1),
    [],
  );

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setActionErr(null);
      setPending(true);
      try {
        await fn();
        refetch();
      } catch (e) {
        if (e instanceof ApiError) setActionErr(`${e.errorCode}: ${e.message}`);
        else if (e instanceof Error) setActionErr(e.message);
        else setActionErr(String(e));
      } finally {
        setPending(false);
      }
    },
    [refetch],
  );

  if (mock) return null;
  if (!departmentId) {
    return (
      <div className="surface border border-token rounded-lg p-4 m-5">
        <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4" /> Live · service-роли каталог
        </div>
        <div className="empty-card">
          У персоны нет department_id — каталог ролей привязан к отделу.
        </div>
      </div>
    );
  }

  return (
    <div className="surface border border-token rounded-lg p-4 m-5">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="text-xs uppercase text-dim flex items-center gap-2">
          <ShieldCheck className="w-4 h-4" /> Live · каталог ролей {deptLabel}{" "}
          {deptLabel !== departmentId && (
            <span className="mono normal-case">({departmentId})</span>
          )}
        </div>
        <select
          className="input"
          value={serviceName}
          onChange={(e) => setServiceName(e.target.value as ServiceName)}
        >
          {DEFAULT_SERVICES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {scopeNote && (
        <div className="text-[11px] text-dim mb-2 italic">{scopeNote}</div>
      )}

      {actionErr && <div className="alert-danger mb-2">{actionErr}</div>}

      {rolesQ.loading && <div className="spinner">Загрузка…</div>}
      {rolesQ.error && rolesQ.error instanceof ApiError && (
        <div className="alert-danger">
          {rolesQ.error.errorCode}: {rolesQ.error.message}
          <button className="btn btn-sm ml-2" onClick={refetch}>
            Повторить
          </button>
        </div>
      )}
      {rolesQ.error && !(rolesQ.error instanceof ApiError) && (
        <div className="alert-danger">{rolesQ.error.message}</div>
      )}

      {!rolesQ.loading && !rolesQ.error && (
        <>
          {(rolesQ.data ?? []).length === 0 ? (
            <div className="empty-card">
              В области `({departmentId}, {serviceName})` ролей нет.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-dim text-xs uppercase">
                <tr>
                  <th className="pb-2 pr-3">role_name</th>
                  <th className="pb-2 pr-3">описание</th>
                  <th className="pb-2 pr-3">тип</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {(rolesQ.data ?? []).map((r) => (
                  <tr key={r.role_name} className="border-t border-token">
                    <td className="py-2 mono text-xs">{r.role_name}</td>
                    <td className="text-xs text-dim">{r.description ?? "—"}</td>
                    <td className="text-xs">
                      {r.is_system ? (
                        <span className="badge">system</span>
                      ) : (
                        <span className="badge badge-accent">custom</span>
                      )}
                    </td>
                    <td className="text-xs flex gap-1">
                      <button
                        className="btn btn-sm"
                        disabled={r.is_system || pending}
                        title={
                          r.is_system
                            ? "Системную роль нельзя менять"
                            : "Изменить description"
                        }
                        onClick={() => {
                          const next = window.prompt(
                            "description:",
                            r.description ?? "",
                          );
                          if (next === null) return;
                          run(() =>
                            srApi.patchServiceRole(
                              departmentId,
                              serviceName,
                              r.role_name,
                              { description: next },
                            ),
                          );
                        }}
                      >
                        изменить
                      </button>
                      <button
                        className="btn btn-sm btn-danger"
                        disabled={r.is_system || pending}
                        title={
                          r.is_system ? "Системную роль нельзя удалить" : undefined
                        }
                        onClick={() => {
                          if (!window.confirm(`Удалить роль ${r.role_name}?`)) {
                            return;
                          }
                          run(() =>
                            srApi.deleteServiceRole(
                              departmentId,
                              serviceName,
                              r.role_name,
                            ),
                          );
                        }}
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {canCreate && (
            <div className="mt-4 border-t border-token pt-3">
              <div className="text-xs uppercase text-dim mb-2 flex items-center gap-1">
                <Plus className="w-3 h-3" /> Создать роль в `(dept={departmentId}, svc={serviceName})`
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  className="input mono"
                  placeholder="role_name"
                  value={newRoleName}
                  onChange={(e) => setNewRoleName(e.target.value)}
                />
                <input
                  className="input"
                  placeholder="description (опц.)"
                  value={newRoleDesc}
                  onChange={(e) => setNewRoleDesc(e.target.value)}
                />
              </div>
              <button
                className="btn btn-primary mt-2 flex items-center gap-1"
                disabled={pending || !newRoleName.trim()}
                onClick={() => {
                  const roleName = newRoleName.trim();
                  const nameErr = validateRoleName(roleName);
                  if (nameErr) {
                    setActionErr(nameErr);
                    return;
                  }
                  void run(async () => {
                    await srApi.createServiceRole(departmentId, serviceName, {
                      role_name: roleName,
                      description: newRoleDesc.trim() || undefined,
                    });
                    setNewRoleName("");
                    setNewRoleDesc("");
                  });
                }}
              >
                <Plus className="w-4 h-4" /> Создать
              </button>
            </div>
          )}

          <div className="mt-4 border-t border-token pt-3">
            <div className="text-xs uppercase text-dim mb-2">
              Массовое назначение / отзыв
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input
                className="input mono"
                placeholder="role_name"
                value={bulkRole}
                onChange={(e) => setBulkRole(e.target.value)}
              />
              <input
                className="input mono"
                placeholder="user_ids csv (usr_a,usr_b)"
                value={bulkUserIds}
                onChange={(e) => setBulkUserIds(e.target.value)}
              />
            </div>
            <div className="flex gap-2 mt-2">
              <button
                className="btn btn-primary flex items-center gap-1"
                disabled={pending || !bulkRole.trim() || !bulkUserIds.trim()}
                onClick={() => {
                  const ids = bulkUserIds
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean);
                  run(() =>
                    srApi.bulkAssignServiceRole(
                      departmentId,
                      serviceName,
                      bulkRole.trim(),
                      { user_ids: ids },
                    ),
                  );
                }}
              >
                <UserPlus className="w-4 h-4" /> назначить
              </button>
              <button
                className="btn btn-danger flex items-center gap-1"
                disabled={pending || !bulkRole.trim() || !bulkUserIds.trim()}
                onClick={() => {
                  const ids = bulkUserIds
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean);
                  run(() =>
                    srApi.bulkRevokeServiceRole(
                      departmentId,
                      serviceName,
                      bulkRole.trim(),
                      { user_ids: ids },
                    ),
                  );
                }}
              >
                <UserMinus className="w-4 h-4" /> отозвать
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
