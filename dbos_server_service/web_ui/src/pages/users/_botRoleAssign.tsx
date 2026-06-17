import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { listServiceRoles } from "@/api/auth/service_roles";
import { useServiceLabel } from "@/lib/labels";
import { ApiError } from "@/api/client";
import type { ServiceName, ServiceRole } from "@/api/auth/types";

/**
 * Assign service-roles to a bot — service picker driven by bot.allowed_services,
 * roles list driven by the (department, service) role catalogue. Replace-
 * семантика по контракту backend (POST /bots/{id}/roles).
 *
 * Если allowed_services пуст — кнопка ассайна задизейблена и подсвечивается
 * подсказка, что сперва надо расширить scope бота.
 */
export function BotRoleAssign({
  departmentId,
  allowedServices,
  alreadyAssigned,
  currentRoles,
  disabled,
  reason,
  onAssign,
}: {
  departmentId: string | null | undefined;
  allowedServices: ServiceName[];
  /** service_name'ы, по которым у бота уже есть назначения — чтобы подсветить replace. */
  alreadyAssigned: Set<string>;
  /**
   * Текущие роли по сервисам (service_name → role_name[]). На старте формы и
   * при смене сервиса этими ролями предзаполняется мультивыбор, чтобы было
   * видно, что уже выдано, а сохранение работало как правка, а не как
   * случайная замена «с нуля».
   */
  currentRoles?: Record<string, string[]>;
  disabled: boolean;
  reason?: string;
  onAssign: (service: ServiceName, roles: string[]) => void;
}) {
  const [service, setService] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Авто-выбор первого допустимого сервиса.
  useEffect(() => {
    if (!service && allowedServices.length > 0) {
      setService(allowedServices[0]);
    }
  }, [service, allowedServices]);

  const catalogQ = useQuery<ServiceRole[]>(
    () => listServiceRoles(departmentId!, service),
    [departmentId, service],
    { enabled: !!departmentId && !!service },
  );

  // При смене сервиса подставляем текущие роли этого сервиса как стартовый
  // выбор — пользователь видит, что уже выдано, и снимает/добавляет галочки.
  useEffect(() => {
    setSelected(new Set(currentRoles?.[service] ?? []));
  }, [service, currentRoles]);

  const roles = catalogQ.data ?? [];
  const isReplace = service && alreadyAssigned.has(service);
  const current = currentRoles?.[service] ?? [];
  const currentSet = new Set(current);
  // Изменился ли набор относительно текущего — чтобы не слать no-op replace.
  const dirty =
    selected.size !== currentSet.size ||
    Array.from(selected).some((r) => !currentSet.has(r));

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  if (allowedServices.length === 0) {
    return (
      <div className="mt-3 text-xs text-dim italic">
        У бота пустой allowed_services — расширьте scope перед назначением ролей.
      </div>
    );
  }

  return (
    <div className="mt-3 border-t border-token pt-3 flex flex-col gap-2">
      <div className="text-xs uppercase text-dim flex items-center gap-2">
        <ShieldCheck className="w-3 h-3" /> Назначить роли
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-xs text-dim">service</label>
        <select
          className="input"
          value={service}
          onChange={(e) => setService(e.target.value)}
          disabled={disabled}
        >
          {allowedServices.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {isReplace && (
          <span className="badge badge-warn text-[10px]" title="Уже есть назначения по этому сервису — будут заменены">
            replace
          </span>
        )}
      </div>

      {!departmentId && (
        <div className="text-xs text-dim italic">department_id неизвестен — каталог ролей недоступен.</div>
      )}
      {catalogQ.loading && (
        <div className="text-xs text-dim">загрузка каталога ролей…</div>
      )}
      {catalogQ.error && (
        <div className="alert-danger text-xs">
          {catalogQ.error instanceof ApiError
            ? `${catalogQ.error.errorCode}: ${catalogQ.error.message}`
            : catalogQ.error.message}
        </div>
      )}
      {!catalogQ.loading && !catalogQ.error && roles.length === 0 && service && (
        <div className="text-xs text-dim italic">
          В каталоге (dept={departmentId}, service={service}) нет ролей.
          Создайте их в разделе «Сервисы → роли».
        </div>
      )}

      {roles.length > 0 && (
        <div className="flex flex-wrap gap-2 max-h-40 overflow-y-auto p-2 surface-2 rounded border border-token">
          {roles.map((r) => {
            const on = selected.has(r.role_name);
            const isCurrent = currentSet.has(r.role_name);
            return (
              <label
                key={r.role_name}
                className={`flex items-center gap-1 text-xs cursor-pointer px-2 py-1 rounded border ${
                  on ? "border-accent" : "border-token"
                }`}
                title={
                  isCurrent
                    ? `Сейчас выдана. ${r.description ?? r.role_name}`
                    : (r.description ?? r.role_name)
                }
              >
                <input
                  type="checkbox"
                  checked={on}
                  disabled={disabled}
                  onChange={() => toggle(r.role_name)}
                />
                <span className="mono">{r.role_name}</span>
                {isCurrent && (
                  <span className="badge badge-accent text-[10px]">сейчас</span>
                )}
                {r.is_system && (
                  <span className="badge text-[10px]">system</span>
                )}
              </label>
            );
          })}
        </div>
      )}

      {isReplace && (
        <div className="text-[11px] text-dim">
          Сохранение заменит весь набор ролей по сервису{" "}
          <span className="mono">{service}</span> на отмеченный — снятые галочки
          будут отозваны.
        </div>
      )}

      <div className="flex justify-end">
        <button
          className="btn btn-primary flex items-center gap-1"
          disabled={disabled || !service || (isReplace ? !dirty : selected.size === 0)}
          title={
            isReplace && !dirty
              ? "Набор ролей не изменился"
              : reason
          }
          onClick={() => {
            onAssign(service as ServiceName, Array.from(selected));
          }}
        >
          <ShieldCheck className="w-4 h-4" />
          {isReplace ? "Сохранить роли" : "Назначить роли"}
        </button>
      </div>
    </div>
  );
}

/** Sub-label render для service_name в таблицах. */
export function ServiceInlineLabel({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}
