import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";
import { ApiError } from "@/api/client";
import { useServiceLabel } from "@/lib/labels";
import { BotRoleAssign } from "@/pages/users/_botRoleAssign";
import type { BotRoleResponse } from "@/api/auth/types";

/**
 * Тело карточки service-ролей бота: предупреждение про orphan-роли вне
 * allowed_services, таблица service → roles с revoke и форма назначения новой
 * роли через BotRoleAssign. Обёртку (card/surface) и заголовок секции рисует
 * вызывающая страница — здесь только общая внутренность, идентичная между
 * /bots и admin/services.
 */
export function BotRolesPanel({
  roles,
  allowedServices,
  departmentId,
  loading,
  error,
  canEdit,
  canManage,
  reason,
  pending,
  onRevoke,
  onAssign,
}: {
  roles: BotRoleResponse[];
  allowedServices: string[];
  departmentId: string | null;
  loading: boolean;
  error: Error | null;
  canEdit: boolean;
  canManage: boolean;
  reason?: string;
  pending: boolean;
  onRevoke: (serviceName: string) => void;
  onAssign: (service: string, roles: string[]) => void;
}) {
  const allowedSet = useMemo(
    () => new Set(allowedServices),
    [allowedServices],
  );
  const orphanRoles = useMemo(
    () => roles.filter((r) => !allowedSet.has(r.service_name)),
    [roles, allowedSet],
  );
  const currentRoles = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const r of roles) map[r.service_name] = r.roles;
    return map;
  }, [roles]);

  return (
    <>
      {error && (
        <div className="alert-danger mb-2">
          {error instanceof ApiError
            ? `${error.errorCode}: ${error.message}`
            : error.message}
        </div>
      )}
      {!loading && roles.length === 0 && (
        <div className="empty-card text-sm">Роли боту не выданы.</div>
      )}
      {orphanRoles.length > 0 && (
        <div className="alert-danger mb-2 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-[2px]" />
          <div className="text-sm">
            {orphanRoles.length} назнач.{" "}
            {orphanRoles.map((r) => r.service_name).join(", ")} вне
            allowed_services — бот эти роли не применит. Верните сервис в
            allowed_services или отзовите роль.
          </div>
        </div>
      )}
      {roles.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">service</th>
              <th className="pb-2 pr-3">roles</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {roles.map((r) => {
              const orphan = !allowedSet.has(r.service_name);
              return (
                <tr
                  key={r.service_name}
                  className="border-t border-token align-top"
                >
                  <td className="py-2 text-xs">
                    <ServiceInline name={r.service_name} />
                    {orphan && (
                      <span className="badge badge-warn ml-1 text-[10px]">
                        вне scope
                      </span>
                    )}
                  </td>
                  <td className="text-xs">
                    <div className="flex flex-wrap gap-1">
                      {r.roles.map((role) => (
                        <span key={role} className="badge badge-accent">
                          {role}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="text-right">
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={!canManage || pending}
                      title={canManage ? undefined : reason}
                      onClick={() => onRevoke(r.service_name)}
                    >
                      revoke
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {canEdit && (
        <BotRoleAssign
          departmentId={departmentId}
          allowedServices={allowedServices}
          alreadyAssigned={new Set(roles.map((r) => r.service_name))}
          currentRoles={currentRoles}
          disabled={!canManage || pending}
          reason={canManage ? undefined : reason}
          onAssign={onAssign}
        />
      )}
    </>
  );
}

function ServiceInline({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}
