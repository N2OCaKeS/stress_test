import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Activity, AlertTriangle, Server, ShieldCheck } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { canManageHostServices } from "@/lib/rbac";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  checkAllServices,
  type HealthState,
  type ServiceHealth,
} from "@/api/health";
import { getHostServices, controlHostService } from "@/api/server/misc";
import type {
  AlltaHostServiceItem,
  AstraHostServiceItem,
  HostServiceControlAction,
  HostServicesResponse,
} from "@/api/server/types";

const POLL_INTERVAL_MS = 60_000;

/** `/admin/${id}` каталога — гейтится `canManageHostServices`, см. adminCatalog.ts. */
const HOST_CONTROL_SETTINGS_PATH = "/admin/services.server.host_control";

type RowStatus = "ok" | "fail" | "unknown";

interface HealthRow {
  id: string;
  service: string;
  status: RowStatus;
  checked_at: string | null;
  latency: string;
  error: string | null;
  /** id systemd-юнита — задан только у управляемых ALLTA-строк с хоста. */
  unit?: string;
}

function statusFromStates(...states: HealthState[]): "ok" | "fail" {
  return states.every((s) => s === "up") ? "ok" : "fail";
}

function statusBadge(status: RowStatus) {
  if (status === "ok") return <Badge kind="ok">up</Badge>;
  if (status === "unknown") return <Badge kind="warn">unknown</Badge>;
  return <Badge kind="danger">down</Badge>;
}

function fmtTime(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("ru-RU");
}

function fmtLatency(health: number | null, ready: number | null): string {
  const parts = [
    health == null ? null : `health ${health} ms`,
    ready == null ? null : `ready ${ready} ms`,
  ].filter(Boolean);
  return parts.length ? parts.join(" / ") : "-";
}

/** Четыре собственных сервиса emm — прямой fetch health/ready, без SSH. */
function alltaInternalRows(services: ServiceHealth[] | null): HealthRow[] {
  return (services ?? []).map((s) => ({
    id: `internal-${s.id}`,
    service: s.label,
    status: statusFromStates(s.health, s.ready),
    checked_at: s.checked_at,
    latency: fmtLatency(s.health_latency_ms, s.ready_latency_ms),
    error: [s.health_error, s.ready_error].filter(Boolean).join(" / ") || null,
  }));
}

function unitRowStatus(status: AlltaHostServiceItem["status"]): RowStatus {
  if (status === "up") return "ok";
  if (status === "unknown") return "unknown";
  return "fail";
}

/** Systemd-юниты, заведённые СВОИМ отделом caller'а, проверяются backend'ом по SSH. */
function alltaUnitRows(items: AlltaHostServiceItem[] | null): HealthRow[] {
  return (items ?? []).map((item) => ({
    id: `unit-${item.id}`,
    service: item.label,
    status: unitRowStatus(item.status),
    checked_at: item.checked_at,
    latency: "-",
    error: item.error,
    unit: item.id,
  }));
}

function astraRowStatus(status: AstraHostServiceItem["status"]): RowStatus {
  if (status === "up") return "ok";
  if (status === "unknown") return "unknown";
  return "fail";
}

function astraRows(items: AstraHostServiceItem[] | null): HealthRow[] {
  return (items ?? []).map((item) => ({
    id: item.id,
    service: item.label,
    status: astraRowStatus(item.status),
    checked_at: item.checked_at,
    latency: item.latency_ms == null ? "-" : `${item.latency_ms} ms`,
    error: item.error,
  }));
}

function controlErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (
      e.errorCode === "HOST_SERVICES_NOT_CONFIGURED" ||
      e.errorCode === "HOST_SERVICE_SSH_UNAVAILABLE"
    ) {
      return "SSH-доступ к хосту не настроен или недоступен — проверьте настройки в «Управление сервисами хоста».";
    }
    if (e.errorCode === "HOST_UNIT_UNKNOWN") {
      return "Юнит не найден — обновите страницу и попробуйте снова.";
    }
    if (e.errorCode === "HOST_SERVICE_CONTROL_FAILED") {
      return "Команда SSH/systemctl не выполнилась на хосте.";
    }
    if (e.status === 403) {
      return "Недостаточно прав для управления сервисами хоста своего отдела.";
    }
  }
  return apiErrMsg(e, "Не удалось выполнить действие");
}

export function ServicesHealth() {
  const { persona } = usePersona();
  const toast = useToast();
  const confirm = useConfirm();
  const canManage = canManageHostServices(persona);
  const hasDept = persona.dept_id != null;

  const [services, setServices] = useState<ServiceHealth[] | null>(null);
  const [hostServices, setHostServices] = useState<HostServicesResponse | null>(null);
  const [pendingUnit, setPendingUnit] = useState<string | null>(null);

  const fetchHostServices = useCallback(async () => {
    try {
      const next = await getHostServices();
      setHostServices(next);
    } catch {
      // Деградируем мягко — строки просто не обновятся до следующего тика.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      const [next] = await Promise.all([checkAllServices(), fetchHostServices()]);
      if (!cancelled) setServices(next);
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [fetchHostServices]);

  const alltaTableRows = useMemo(
    () => [...alltaInternalRows(services), ...alltaUnitRows(hostServices?.allta ?? null)],
    [services, hostServices],
  );
  const astraTableRows = useMemo(
    () => astraRows(hostServices?.astra ?? null),
    [hostServices],
  );
  // Юниты пришли (не первичная загрузка) и список пуст: либо у отдела ещё
  // нет настроек/юнитов, либо SSH недоступен — backend в обоих случаях
  // отдаёт пустой `allta`, различать на UI нечем и незачем.
  const unitsEmpty = hasDept && hostServices != null && hostServices.allta.length === 0;

  async function handleControl(
    unitId: string,
    label: string,
    action: HostServiceControlAction,
  ) {
    if (action !== "start") {
      const verb = action === "stop" ? "Остановить" : "Перезапустить";
      const ok = await confirm.confirm({
        message: `${verb} сервис ${label} на хосте?`,
        danger: true,
        confirmLabel: verb,
      });
      if (!ok) return;
    }
    setPendingUnit(unitId);
    try {
      await controlHostService(unitId, action);
      toast.success(`${label}: команда «${action}» выполнена`);
      await fetchHostServices();
    } catch (err) {
      toast.error(controlErrorMessage(err));
    } finally {
      setPendingUnit(null);
    }
  }

  return (
    <Shell breadcrumb="health / Здоровье служб">
      <main className="flex-1 min-w-0 overflow-auto">
        <div className="p-5 flex flex-col gap-5">
          <h1 className="text-lg font-semibold">Здоровье служб</h1>

          <HealthSection
            icon={ShieldCheck}
            title="ASTRA"
            subtitle="внешние сервисы astralinux.ru"
            rows={astraTableRows}
          />

          <HealthSection
            icon={Activity}
            title="ALLTA"
            subtitle="собственные сервисы платформы и инфраструктура хоста своего отдела"
            rows={alltaTableRows}
            showActions
            canManage={canManage}
            pendingUnit={pendingUnit}
            onControl={handleControl}
            banner={
              !hasDept ? (
                <div className="alert-warn text-xs flex items-center gap-2 px-4 py-2 border-b border-token">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>Вы не привязаны к отделу — юниты хоста ALLTA недоступны.</span>
                </div>
              ) : unitsEmpty ? (
                <div className="alert-warn text-xs flex items-center gap-2 px-4 py-2 border-b border-token">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>
                    Сервисы хоста ещё не настроены для вашего отдела.{" "}
                    {canManage ? (
                      <Link to={HOST_CONTROL_SETTINGS_PATH} className="underline">
                        Настроить
                      </Link>
                    ) : (
                      "Обратитесь к администратору отдела."
                    )}
                  </span>
                </div>
              ) : null
            }
          />
        </div>
      </main>
    </Shell>
  );
}

function HealthSection({
  icon: Icon,
  title,
  subtitle,
  rows,
  showActions = false,
  canManage = false,
  pendingUnit = null,
  onControl,
  banner,
}: {
  icon: typeof Activity;
  title: string;
  subtitle: string;
  rows: HealthRow[];
  showActions?: boolean;
  canManage?: boolean;
  pendingUnit?: string | null;
  onControl?: (
    unitId: string,
    label: string,
    action: HostServiceControlAction,
  ) => void;
  banner?: ReactNode;
}) {
  const colCount = showActions ? 6 : 5;
  return (
    <section className="surface border border-token rounded">
      <div className="p-4 border-b border-token flex items-center gap-3">
        <div className="h-9 w-9 rounded bg-accent/10 border border-token flex items-center justify-center">
          <Icon className="w-5 h-5 text-accent" />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold truncate">{title}</h2>
          <div className="text-xs text-dim">{subtitle}</div>
        </div>
      </div>
      {banner}
      <div className="overflow-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-dim uppercase border-b border-token">
            <tr>
              <th className="text-left font-medium px-4 py-2">Сервис</th>
              <th className="text-left font-medium px-4 py-2">Статус</th>
              <th className="text-left font-medium px-4 py-2">
                Время подключения
              </th>
              <th className="text-left font-medium px-4 py-2">Latency</th>
              <th className="text-left font-medium px-4 py-2">Ошибка</th>
              {showActions && (
                <th className="text-left font-medium px-4 py-2">Управление</th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={colCount} className="px-4 py-6 text-dim">
                  Проверка служб выполняется...
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id} className="border-b border-token last:border-0">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Server className="w-4 h-4 text-dim shrink-0" />
                      <span className="mono">{row.service}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">{statusBadge(row.status)}</td>
                  <td className="px-4 py-3 text-dim">
                    {fmtTime(row.checked_at)}
                  </td>
                  <td className="px-4 py-3 text-dim">{row.latency}</td>
                  <td className="px-4 py-3">
                    {row.error ? (
                      <span className="text-danger">{row.error}</span>
                    ) : (
                      <span className="text-dim">-</span>
                    )}
                  </td>
                  {showActions && (
                    <td className="px-4 py-3">
                      {row.unit ? (
                        <ServiceControls
                          unitId={row.unit}
                          label={row.service}
                          canManage={canManage}
                          busy={pendingUnit === row.unit}
                          onControl={onControl!}
                        />
                      ) : (
                        <span className="text-dim">-</span>
                      )}
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ServiceControls({
  unitId,
  label,
  canManage,
  busy,
  onControl,
}: {
  unitId: string;
  label: string;
  canManage: boolean;
  busy: boolean;
  onControl: (
    unitId: string,
    label: string,
    action: HostServiceControlAction,
  ) => void;
}) {
  const disabled = !canManage || busy;
  const title = canManage
    ? undefined
    : "Доступно только department_admin или server_service.admin своего отдела";
  return (
    <div className="flex items-center gap-1.5">
      <Button
        size="sm"
        disabled={disabled}
        title={title}
        onClick={() => onControl(unitId, label, "start")}
      >
        Start
      </Button>
      <Button
        variant="danger"
        size="sm"
        disabled={disabled}
        title={title}
        onClick={() => onControl(unitId, label, "stop")}
      >
        Stop
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={disabled}
        title={title}
        onClick={() => onControl(unitId, label, "restart")}
      >
        Restart
      </Button>
    </div>
  );
}
