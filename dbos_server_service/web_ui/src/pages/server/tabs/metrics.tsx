import { useMemo, useState } from "react";
import { RotateCcw } from "lucide-react";
import type { EntityRef } from "./_entity";

type GrafanaPanelKind =
  | "cpu"
  | "ram"
  | "temp"
  | "disk"
  | "load"
  | "iowait"
  | "disk-io"
  | "net"
  | "tcp";

interface PanelMeta {
  panelId: number;
  title: string;
  label: string;
}

const PANEL_MAP: Record<GrafanaPanelKind, PanelMeta> = {
  cpu: { panelId: 10, title: "CPU usage (%)", label: "CPU" },
  ram: { panelId: 8, title: "RAM information", label: "RAM" },
  temp: { panelId: 12, title: "Hardware temperature", label: "Temp" },
  disk: { panelId: 19, title: "Disk Utilization", label: "Disk" },
  load: { panelId: 11, title: "Average system load", label: "Load" },
  iowait: { panelId: 20, title: "CPU iowait (30s)", label: "IO wait" },
  "disk-io": { panelId: 13, title: "Disk I/O read / write (bytes)", label: "Disk I/O" },
  net: { panelId: 17, title: "Network traffic", label: "Net" },
  tcp: { panelId: 16, title: "TCP connections", label: "TCP" },
};

const DEFAULT_GRAFANA_URL = "http://10.177.103.10:3000";
const DASHBOARD_UID = "fecn0mamdcsg0f";
const DASHBOARD_SLUG = "allta-dashboard";
const PRIMARY_PANELS: GrafanaPanelKind[] = ["cpu", "ram", "temp", "disk"];
const DETAIL_PANELS: GrafanaPanelKind[] = ["load", "iowait", "disk-io", "net", "tcp"];

interface MetricsTabProps {
  entity: EntityRef;
}

export function MetricsTab({ entity }: MetricsTabProps) {
  const target = metricsTarget(entity);
  const [mode, setMode] = useState<"grid" | "single">("grid");
  const [selected, setSelected] = useState<GrafanaPanelKind>("cpu");
  const grafanaBase = useMemo(() => grafanaBaseUrl(), []);

  if (!target.ip) {
    return (
      <div className="p-5">
        <div className="panel p-4 text-sm text-dim">
          IP адрес {target.kind === "vm" ? "ВМ" : "сервера"} неизвестен.
          Grafana-панели появятся после настройки сети и инвентаризации.
        </div>
      </div>
    );
  }
  const targetIp = target.ip;

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
          <div className="segmented">
            <button
              type="button"
              className={mode === "grid" ? "active" : ""}
              onClick={() => setMode("grid")}
            >
              Сетка
            </button>
            <button
              type="button"
              className={mode === "single" ? "active" : ""}
              onClick={() => setMode("single")}
            >
              Одна панель
            </button>
          </div>
        </div>
      </div>

      {mode === "single" ? (
        <div className="grid gap-3 lg:grid-cols-[11rem_minmax(0,1fr)]">
          <div className="panel p-2 flex flex-col gap-2">
            {[PRIMARY_PANELS, DETAIL_PANELS].map((group, idx) => (
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
          <GrafanaPanel baseUrl={grafanaBase} targetIp={targetIp} panel={selected} height={430} />
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
            />
          ))}
        </div>
      )}
    </div>
  );
}

function GrafanaPanel({
  baseUrl,
  targetIp,
  panel,
  height,
}: {
  baseUrl: string;
  targetIp: string;
  panel: GrafanaPanelKind;
  height: number;
}) {
  const [resetTick, setResetTick] = useState(0);
  const meta = PANEL_MAP[panel];
  const params = new URLSearchParams({
    orgId: "1",
    "var-node": `${targetIp}:9100`,
    panelId: String(meta.panelId),
    theme: "dark",
    refresh: "10s",
  });
  const src = `${baseUrl}/d-solo/${DASHBOARD_UID}/${DASHBOARD_SLUG}?${params.toString()}`;

  return (
    <div className="panel relative overflow-hidden p-2">
      <iframe
        key={resetTick}
        data-testid={`grafana-panel-${panel}`}
        title={meta.title}
        src={src}
        width="100%"
        height={height}
        frameBorder="0"
        className="rounded bg-bg"
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

function grafanaBaseUrl(): string {
  const raw = import.meta.env.VITE_GRAFANA_URL;
  return (typeof raw === "string" && raw.trim() ? raw : DEFAULT_GRAFANA_URL).replace(/\/+$/, "");
}

function metricsTarget(entity: EntityRef): {
  kind: EntityRef["kind"];
  label: string;
  ip: string | null;
} {
  if (entity.kind === "server") {
    return {
      kind: "server",
      label: entity.server.display_name || entity.server.hostname,
      ip: entity.server.ip_address,
    };
  }
  return {
    kind: "vm",
    label: entity.vm.hostname || entity.vm.name,
    ip: entity.vm.ip_address,
  };
}
