import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Gauge,
  Grid2X2,
  LayoutDashboard,
  PanelTop,
  RotateCcw,
} from "lucide-react";
import { apiErrMsg } from "@/api/client";
import { installNodeExporter } from "@/api/server/servers";
import { installNodeExporterVm } from "@/api/server/vms";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useTheme } from "@/contexts/ThemeContext";
import { useToast } from "@/contexts/ToastContext";
import type { ThemeName } from "@/types/persona";
import type { EntityRef } from "./_entity";

type GrafanaPanelKind =
  | "runtime"
  | "cpu-count"
  | "total-ram"
  | "cpu"
  | "ram"
  | "temp"
  | "disk"
  | "disk-space"
  | "load"
  | "iowait"
  | "fd"
  | "disk-io"
  | "disk-time"
  | "disk-iops"
  | "net"
  | "tcp"
  | "softnet";

interface PanelMeta {
  panelId: number;
  title: string;
  label: string;
  gaugePanelId?: number;
}

const PANEL_MAP: Record<GrafanaPanelKind, PanelMeta> = {
  runtime: { panelId: 1, title: "System runtime", label: "Runtime" },
  "cpu-count": { panelId: 2, title: "CPU Audit number", label: "CPU count" },
  "total-ram": { panelId: 3, title: "Total RAM", label: "Total RAM" },
  cpu: { panelId: 10, title: "CPU usage (%)", label: "CPU", gaugePanelId: 5 },
  ram: { panelId: 8, title: "RAM information", label: "RAM", gaugePanelId: 7 },
  temp: { panelId: 12, title: "Hardware temperature", label: "Temp" },
  disk: { panelId: 19, title: "Disk Utilization", label: "Disk", gaugePanelId: 9 },
  "disk-space": { panelId: 4, title: "Total disk space", label: "Disk space" },
  load: { panelId: 11, title: "Average system load", label: "Load", gaugePanelId: 111 },
  iowait: { panelId: 20, title: "CPU iowait (30s)", label: "IO wait", gaugePanelId: 6 },
  fd: { panelId: 18, title: "Currently open file descriptor", label: "Open FD" },
  "disk-io": { panelId: 13, title: "Disk I/O read / write (bytes)", label: "Disk I/O" },
  "disk-time": { panelId: 14, title: "Disk IO read / write (time)", label: "Disk time" },
  "disk-iops": { panelId: 15, title: "Disk read / write rate (IOPS)", label: "Disk IOPS" },
  net: { panelId: 17, title: "Network traffic", label: "Net" },
  tcp: { panelId: 16, title: "TCP connections", label: "TCP" },
  softnet: { panelId: 21, title: "Softnet Packets", label: "Softnet" },
};

const DEFAULT_GRAFANA_URL = "http://10.177.103.10:18181";
const LOCAL_GRAFANA_URL = "http://localhost:3000";
const LOCAL_NODE_EXPORTER_IP = "10.210.91.10";
const DASHBOARD_UID = "fecn0mamdcsg0f";
const DASHBOARD_SLUG = "allta-dashboard";
const THEMED_DASHBOARDS: Record<
  ThemeName,
  { uid: string; slug: string; grafanaTheme: "dark" | "light"; dbosTheme: ThemeName }
> = {
  "vscode-dark": {
    uid: "dbos-node-vscode-dark",
    slug: "allta-dashboard-vscode-dark",
    grafanaTheme: "dark",
    dbosTheme: "vscode-dark",
  },
  "vscode-light": {
    uid: "dbos-node-vscode-light",
    slug: "allta-dashboard-vscode-light",
    grafanaTheme: "light",
    dbosTheme: "vscode-light",
  },
  "dark-orange": {
    uid: "dbos-node-dark-orange",
    slug: "allta-dashboard-dark-orange",
    grafanaTheme: "dark",
    dbosTheme: "dark-orange",
  },
  blue: {
    uid: "dbos-node-blue",
    slug: "allta-dashboard-blue",
    grafanaTheme: "dark",
    dbosTheme: "blue",
  },
};
const THEMED_FULL_DASHBOARDS: Record<
  ThemeName,
  { uid: string; slug: string; grafanaTheme: "dark" | "light"; dbosTheme: ThemeName }
> = {
  "vscode-dark": {
    uid: "dbos-node-full-vscode-dark",
    slug: "node-exporter-full-vscode-dark",
    grafanaTheme: "dark",
    dbosTheme: "vscode-dark",
  },
  "vscode-light": {
    uid: "dbos-node-full-vscode-light",
    slug: "node-exporter-full-vscode-light",
    grafanaTheme: "light",
    dbosTheme: "vscode-light",
  },
  "dark-orange": {
    uid: "dbos-node-full-dark-orange",
    slug: "node-exporter-full-dark-orange",
    grafanaTheme: "dark",
    dbosTheme: "dark-orange",
  },
  blue: {
    uid: "dbos-node-full-blue",
    slug: "node-exporter-full-blue",
    grafanaTheme: "dark",
    dbosTheme: "blue",
  },
};
const PRIMARY_PANELS: GrafanaPanelKind[] = ["cpu", "ram", "temp", "disk"];
const SUMMARY_PANELS: GrafanaPanelKind[] = [
  "runtime",
  "cpu-count",
  "total-ram",
  "disk-space",
  "fd",
];
const DETAIL_PANELS: GrafanaPanelKind[] = [
  "load",
  "iowait",
  "disk-io",
  "disk-time",
  "disk-iops",
  "net",
  "tcp",
  "softnet",
];
const GAUGE_PANELS: GrafanaPanelKind[] = ["cpu", "ram", "disk", "iowait"];
type GrafanaMode = "infocollector" | "direct";
type GrafanaViewMode = "grid" | "single" | "gauges" | "full";

const VIEW_MODE_STORAGE_KEY = "dbos:grafana:view-mode";
const SELECTED_PANEL_STORAGE_KEY = "dbos:grafana:selected-panel";

interface MetricsTabProps {
  entity: EntityRef;
}

export function MetricsTab({ entity }: MetricsTabProps) {
  const target = metricsTarget(entity);
  const targetIp = target.ip ? stripCidr(target.ip) : null;
  const [mode, setModeState] = useState<GrafanaViewMode>(() =>
    readGrafanaViewMode(),
  );
  const [selected, setSelectedState] = useState<GrafanaPanelKind>(() =>
    readSelectedPanel(),
  );
  const [installing, setInstalling] = useState(false);
  const toast = useToast();
  const { confirm } = useConfirm();
  const { theme } = useTheme();
  const grafanaBase = useMemo(() => grafanaBaseUrl(targetIp), [targetIp]);
  const grafanaMode = useMemo(
    () => resolveGrafanaMode(grafanaBase, targetIp),
    [grafanaBase, targetIp],
  );
  const dashboard = resolveDashboard(grafanaBase, theme);
  const fullDashboard = resolveFullDashboard(grafanaBase, theme);
  const grafanaStatus = useGrafanaAvailability(grafanaBase, grafanaMode, targetIp);

  function setMode(next: GrafanaViewMode) {
    setModeState(next);
    writeStorage(VIEW_MODE_STORAGE_KEY, next);
  }

  function setSelected(next: GrafanaPanelKind) {
    setSelectedState(next);
    writeStorage(SELECTED_PANEL_STORAGE_KEY, next);
  }

  if (!targetIp) {
    return (
      <div className="p-5">
        <div className="panel p-4 text-sm text-dim">
          IP адрес {target.kind === "vm" ? "ВМ" : "сервера"} неизвестен.
          Grafana-панели появятся после настройки сети и инвентаризации.
        </div>
      </div>
    );
  }

  async function handleInstallNodeExporter() {
    if (installing || !target.id) return;
    if (
      !(await confirm({
        title: "Установить node_exporter",
        message: `Установить node_exporter на ${target.label}? После этого Prometheus/Grafana смогут собирать метрики с ${targetIp}:9100.`,
        confirmLabel: "Установить",
      }))
    )
      return;
    setInstalling(true);
    try {
      const res =
        target.kind === "server"
          ? await installNodeExporter(target.id)
          : await installNodeExporterVm(target.id);
      toast.success(`node_exporter: задача поставлена ${res.task_id}`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось поставить node_exporter"));
    } finally {
      setInstalling(false);
    }
  }

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="panel p-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">Grafana</h2>
            <div className="text-xs text-dim">
              {target.label} · <span className="mono">{targetIp}:9100</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {grafanaMode === "direct" && (
              <>
                <button
                  type="button"
                  className={`btn btn-sm flex items-center gap-1 ${mode === "grid" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setMode("grid")}
                  title="Показать основные графики сеткой"
                >
                  <Grid2X2 className="w-4 h-4" />
                  Сетка
                </button>
                <button
                  type="button"
                  className={`btn btn-sm flex items-center gap-1 ${mode === "single" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setMode("single")}
                  title="Показать один выбранный график"
                >
                  <PanelTop className="w-4 h-4" />
                  Одна панель
                </button>
                <button
                  type="button"
                  className={`btn btn-sm flex items-center gap-1 ${mode === "gauges" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setMode("gauges")}
                  title="Показать основные показатели спидометрами"
                >
                  <Gauge className="w-4 h-4" />
                  Спидометры
                </button>
                <button
                  type="button"
                  className={`btn btn-sm flex items-center gap-1 ${mode === "full" ? "btn-primary" : "btn-ghost"}`}
                  onClick={() => setMode("full")}
                  title="Показать полный Node Exporter dashboard из infocollector"
                >
                  <LayoutDashboard className="w-4 h-4" />
                  Полный
                </button>
              </>
            )}
            <button
              type="button"
              className="btn btn-sm flex items-center gap-1"
              onClick={handleInstallNodeExporter}
              disabled={installing}
              title="Установить node_exporter на целевую машину"
            >
              <Gauge className="w-4 h-4" />
              {installing ? "Запускаем..." : "node_exporter"}
            </button>
          </div>
        </div>
      </div>

      {grafanaStatus === "unavailable" && (
        <GrafanaUnavailable
          baseUrl={grafanaBase}
          targetIp={targetIp}
          installing={installing}
          onInstallNodeExporter={handleInstallNodeExporter}
        />
      )}

      {grafanaStatus === "checking" && (
        <div className="panel p-4 text-sm text-dim">
          Проверяем доступность Grafana...
        </div>
      )}

      {grafanaStatus === "available" &&
        grafanaMode === "infocollector" && (
          <InfocollectorDashboard
            baseUrl={grafanaBase}
            targetIp={targetIp}
            targetLabel={target.label}
            height={720}
          />
        )}

      {grafanaStatus === "available" &&
        grafanaMode === "direct" &&
        (mode === "full" ? (
          <GrafanaDashboard
            baseUrl={grafanaBase}
            targetIp={targetIp}
            targetLabel={target.label}
            dashboard={fullDashboard}
            height={920}
          />
        ) : mode === "gauges" ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {GAUGE_PANELS.map((panel) => (
              <GrafanaPanel
                key={panel}
                baseUrl={grafanaBase}
                targetIp={targetIp}
                panel={panel}
                height={270}
                dashboard={dashboard}
                variant="gauge"
              />
            ))}
          </div>
        ) : mode === "single" ? (
          <div className="grid gap-3 lg:grid-cols-[11rem_minmax(0,1fr)]">
            <div className="panel p-2 flex flex-col gap-2">
              {[PRIMARY_PANELS, SUMMARY_PANELS, DETAIL_PANELS].map((group, idx) => (
                <div key={idx} className="flex flex-col gap-1">
                  {group.map((panel) => (
                    <button
                      key={panel}
                      type="button"
                      className={`btn justify-start ${selected === panel ? "btn-primary" : ""}`}
                      onClick={() => setSelected(panel)}
                    >
                      {PANEL_MAP[panel].label}
                    </button>
                  ))}
                </div>
              ))}
            </div>
            <GrafanaPanel
              baseUrl={grafanaBase}
              targetIp={targetIp}
              panel={selected}
              height={430}
              dashboard={dashboard}
            />
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {PRIMARY_PANELS.map((panel) => (
              <GrafanaPanel
                key={panel}
                baseUrl={grafanaBase}
                targetIp={targetIp}
                panel={panel}
                height={360}
                dashboard={dashboard}
              />
            ))}
          </div>
        ))}
    </div>
  );
}

function GrafanaUnavailable({
  baseUrl,
  targetIp,
  installing,
  onInstallNodeExporter,
}: {
  baseUrl: string;
  targetIp: string;
  installing: boolean;
  onInstallNodeExporter: () => void;
}) {
  return (
    <div className="panel p-4">
      <div className="alert alert-warn flex items-start gap-3">
        <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
        <div className="flex-1 text-sm">
          <div className="font-medium">Grafana недоступна</div>
          <div className="text-dim mt-1">
            Добавьте или запустите Grafana по адресу{" "}
            <span className="mono">{baseUrl}</span>. Для метрик этой машины
            также нужен node_exporter на <span className="mono">{targetIp}:9100</span>.
          </div>
          <button
            type="button"
            className="btn btn-primary mt-3 flex items-center gap-1"
            onClick={onInstallNodeExporter}
            disabled={installing}
          >
            <Gauge className="w-4 h-4" />
            {installing ? "Запускаем..." : "Установить node_exporter"}
          </button>
        </div>
      </div>
    </div>
  );
}

function useGrafanaAvailability(
  baseUrl: string,
  mode: GrafanaMode,
  targetIp: string | null,
): "checking" | "available" | "unavailable" {
  const [status, setStatus] = useState<"checking" | "available" | "unavailable">(
    "checking",
  );

  useEffect(() => {
    let cancelled = false;
    setStatus("checking");
    const timeout = window.setTimeout(() => {
      if (!cancelled) setStatus("unavailable");
    }, 3_000);
    if (mode === "infocollector") {
      if (!targetIp) {
        setStatus("unavailable");
        window.clearTimeout(timeout);
        return () => {
          cancelled = true;
          window.clearTimeout(timeout);
        };
      }
      fetch(infocollectorDashboardUrl(baseUrl, targetIp, "allta"), {
        method: "GET",
        mode: "no-cors",
      })
        .then(() => {
          if (!cancelled) setStatus("available");
        })
        .catch(() => {
          if (!cancelled) setStatus("unavailable");
        })
        .finally(() => window.clearTimeout(timeout));
      return () => {
        cancelled = true;
        window.clearTimeout(timeout);
      };
    }

    fetch(`${baseUrl}/api/health`, {
      method: "GET",
      mode: "no-cors",
    })
      .then(() => {
        if (!cancelled) setStatus("available");
      })
      .catch(() => {
        if (!cancelled) setStatus("unavailable");
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [baseUrl, mode, targetIp]);

  return status;
}

function InfocollectorDashboard({
  baseUrl,
  targetIp,
  targetLabel,
  height,
}: {
  baseUrl: string;
  targetIp: string;
  targetLabel: string;
  height: number;
}) {
  const src = infocollectorDashboardUrl(baseUrl, targetIp, "allta");
  return (
    <div className="panel relative overflow-hidden p-2 bg-[var(--bg)]">
      <iframe
        data-testid="grafana-infocollector-dashboard"
        title={`ALLTA dashboard · ${targetLabel}`}
        src={src}
        width="100%"
        height={height}
        frameBorder="0"
        className="rounded bg-[var(--bg)]"
        style={{ backgroundColor: "var(--bg)" }}
      />
    </div>
  );
}

function GrafanaPanel({
  baseUrl,
  targetIp,
  panel,
  height,
  dashboard,
  variant = "timeseries",
}: {
  baseUrl: string;
  targetIp: string;
  panel: GrafanaPanelKind;
  height: number;
  dashboard: {
    uid: string;
    slug: string;
    grafanaTheme: "dark" | "light";
    dbosTheme: ThemeName;
  };
  variant?: "timeseries" | "gauge";
}) {
  const [resetTick, setResetTick] = useState(0);
  const meta = PANEL_MAP[panel];
  const panelId = variant === "gauge" ? (meta.gaugePanelId ?? meta.panelId) : meta.panelId;
  const params = new URLSearchParams({
    orgId: "1",
    "var-node": `${targetIp}:9100`,
    "var-job": "node_exporter",
    panelId: String(panelId),
    theme: dashboard.grafanaTheme,
    dbosTheme: dashboard.dbosTheme,
    transparent: "true",
    refresh: "10s",
  });
  const src = `${baseUrl}/d-solo/${dashboard.uid}/${dashboard.slug}?${params.toString()}`;

  return (
    <div className="panel relative overflow-hidden p-2 bg-[var(--bg)]">
      <iframe
        key={resetTick}
        data-testid={`grafana-panel-${panel}`}
        title={meta.title}
        src={src}
        width="100%"
        height={height}
        frameBorder="0"
        className="rounded bg-[var(--bg)]"
        style={{ backgroundColor: "var(--bg)" }}
      />
      <button
        type="button"
        className="btn btn-ghost absolute right-3 top-3 h-8 px-2"
        aria-label="Сбросить приближение"
        title="Сбросить приближение графика"
        onClick={() => setResetTick((n) => n + 1)}
      >
        <RotateCcw className="w-4 h-4" />
      </button>
    </div>
  );
}

function GrafanaDashboard({
  baseUrl,
  targetIp,
  targetLabel,
  height,
  dashboard,
}: {
  baseUrl: string;
  targetIp: string;
  targetLabel: string;
  height: number;
  dashboard: {
    uid: string;
    slug: string;
    grafanaTheme: "dark" | "light";
    dbosTheme: ThemeName;
  };
}) {
  const params = new URLSearchParams({
    orgId: "1",
    from: "now-15m",
    to: "now",
    timezone: "browser",
    "var-node": `${targetIp}:9100`,
    "var-job": "node_exporter",
    theme: dashboard.grafanaTheme,
    dbosTheme: dashboard.dbosTheme,
    kiosk: "true",
    refresh: "10s",
  });
  const src = `${baseUrl}/d/${dashboard.uid}/${dashboard.slug}?${params.toString()}`;

  return (
    <div className="panel relative overflow-hidden p-2 bg-[var(--bg)]">
      <iframe
        data-testid="grafana-full-dashboard"
        title={`Node Exporter Full · ${targetLabel}`}
        src={src}
        width="100%"
        height={height}
        frameBorder="0"
        className="rounded bg-[var(--bg)]"
        style={{ backgroundColor: "var(--bg)" }}
      />
    </div>
  );
}

function grafanaBaseUrl(targetIp: string | null): string {
  const raw = import.meta.env.VITE_GRAFANA_URL;
  const base =
    typeof raw === "string" && raw.trim()
      ? raw
      : targetIp === LOCAL_NODE_EXPORTER_IP
        ? LOCAL_GRAFANA_URL
        : DEFAULT_GRAFANA_URL;
  return base.replace(/\/+$/, "");
}

function resolveGrafanaMode(baseUrl: string, targetIp: string | null): GrafanaMode {
  const raw = import.meta.env.VITE_GRAFANA_MODE;
  if (raw === "direct" || raw === "infocollector") return raw;
  if (targetIp === LOCAL_NODE_EXPORTER_IP) return "direct";
  return baseUrl.includes(":18181") ? "infocollector" : "direct";
}

function resolveDashboard(baseUrl: string, theme: ThemeName): {
  uid: string;
  slug: string;
  grafanaTheme: "dark" | "light";
  dbosTheme: ThemeName;
} {
  if (baseUrl === LOCAL_GRAFANA_URL) return THEMED_DASHBOARDS[theme];
  return {
    uid: DASHBOARD_UID,
    slug: DASHBOARD_SLUG,
    grafanaTheme: theme === "vscode-light" ? "light" : "dark",
    dbosTheme: theme,
  };
}

function resolveFullDashboard(baseUrl: string, theme: ThemeName): {
  uid: string;
  slug: string;
  grafanaTheme: "dark" | "light";
  dbosTheme: ThemeName;
} {
  if (baseUrl === LOCAL_GRAFANA_URL) return THEMED_FULL_DASHBOARDS[theme];
  return {
    uid: "rYdddlPWk",
    slug: "node-exporter-full",
    grafanaTheme: theme === "vscode-light" ? "light" : "dark",
    dbosTheme: theme,
  };
}

function infocollectorDashboardUrl(
  baseUrl: string,
  targetIp: string,
  board: "allta" | "full",
): string {
  return `${baseUrl}/rest/api/dashboard/${targetIp}/${board}`;
}

function metricsTarget(entity: EntityRef): {
  kind: EntityRef["kind"];
  id: string;
  label: string;
  ip: string | null;
} {
  if (entity.kind === "server") {
    return {
      kind: "server",
      id: entity.server.id,
      label: entity.server.display_name || entity.server.hostname,
      ip: entity.server.ip_address,
    };
  }
  return {
    kind: "vm",
    id: entity.vm.id,
    label: entity.vm.hostname || entity.vm.name,
    ip: entity.vm.ip_address,
  };
}

function stripCidr(ip: string): string {
  return ip.split("/", 1)[0];
}

function readGrafanaViewMode(): GrafanaViewMode {
  const raw = readStorage(VIEW_MODE_STORAGE_KEY);
  return raw === "single" || raw === "grid" || raw === "gauges" || raw === "full"
    ? raw
    : "grid";
}

function readSelectedPanel(): GrafanaPanelKind {
  const raw = readStorage(SELECTED_PANEL_STORAGE_KEY);
  return raw && raw in PANEL_MAP ? (raw as GrafanaPanelKind) : "cpu";
}

function readStorage(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore private-mode/storage failures; the in-memory state still updates.
  }
}
