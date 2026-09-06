import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Activity, AlertTriangle, Server, ShieldCheck } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
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

/** `/admin/${id}` каталога — гейтится account_admin, см. adminCatalog.ts. */
const HOST_CONTROL_SETTINGS_PATH = "/admin/services.server.host_control";

type RowStatus = "ok" | "fail" | "unknown" | "not_configured";

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
  if (status === "not_configured") return <Badge kind="idle">не настроено</Badge>;
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
  if (status === "not_configured") return "not_configured";
  if (status === "unknown") return "unknown";
  return "fail";
}

/** 12 systemd-юнитов ALLTA на хосте, проверяемых backend'ом по SSH. */
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

function controlErrorMessage(e: unknown): { message: string; notConfigured: boolean } {
  if (e instanceof ApiError && e.errorCode === "HOST_SERVICES_NOT_CONFIGURED") {
    return {
      message:
        "SSH-доступ к хосту не настроен — настройте его в «Управление сервисами хоста».",
      notConfigured: true,
    };
  }
  return {
    message: apiErrMsg(e, "Не удалось выполнить действие"),
    notConfigured: false,
  };
}

export function ServicesHealth() {
  const { persona } = usePersona();
  const toast = useToast();
  const confirm = useConfirm();
  const isAccountAdmin = persona.platform_role === "account_admin";

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
  const hasNotConfigured = (hostServices?.allta ?? []).some(
    (item) => item.status === "not_configured",
  );

  async function handleControl(
    unit: string,
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
    setPendingUnit(unit);
    try {
      await controlHostService(unit, action);
      toast.success(`${label}: команда «${action}» выполнена`);
      await fetchHostServices();
    } catch (err) {
      const { message } = controlErrorMessage(err);
      toast.error(message);
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
            subtitle="собственные сервисы платформы и инфраструктура хоста"
            rows={alltaTableRows}
            showActions
            isAccountAdmin={isAccountAdmin}
            pendingUnit={pendingUnit}
            onControl={handleControl}
            banner={
              isAccountAdmin && hasNotConfigured ? (
                <div className="alert-warn text-xs flex items-center gap-2 px-4 py-2 border-b border-token">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>
                    SSH-доступ к хосту не настроен — управление systemd-юнитами
                    недоступно.{" "}
                    <Link to={HOST_CONTROL_SETTINGS_PATH} className="underline">
                      Настроить SSH
                    </Link>
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
  isAccountAdmin = false,
  pendingUnit = null,
  onControl,
  banner,
}: {
  icon: typeof Activity;
  title: string;
  subtitle: string;
  rows: HealthRow[];
  showActions?: boolean;
  isAccountAdmin?: boolean;
  pendingUnit?: string | null;
  onControl?: (
    unit: string,
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
                          unit={row.unit}
                          label={row.service}
                          isAccountAdmin={isAccountAdmin}
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
  unit,
  label,
  isAccountAdmin,
  busy,
  onControl,
}: {
  unit: string;
  label: string;
  isAccountAdmin: boolean;
  busy: boolean;
  onControl: (
    unit: string,
    label: string,
    action: HostServiceControlAction,
  ) => void;
}) {
  const disabled = !isAccountAdmin || busy;
  const title = isAccountAdmin ? undefined : "Доступно только account_admin";
  return (
    <div className="flex items-center gap-1.5">
      <Button
        size="sm"
        disabled={disabled}
        title={title}
        onClick={() => onControl(unit, label, "start")}
      >
        Start
      </Button>
      <Button
        variant="danger"
        size="sm"
        disabled={disabled}
        title={title}
        onClick={() => onControl(unit, label, "stop")}
      >
        Stop
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={disabled}
        title={title}
        onClick={() => onControl(unit, label, "restart")}
      >
        Restart
      </Button>
    </div>
  );
}
