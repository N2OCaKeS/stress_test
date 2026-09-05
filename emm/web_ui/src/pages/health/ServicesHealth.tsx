import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Activity, Server, ShieldCheck } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Badge } from "@/components/ui/Badge";
import {
  checkAllServices,
  type HealthState,
  type ServiceHealth,
} from "@/api/health";

const POLL_INTERVAL_MS = 20_000;

type HealthScope = "allta" | "astra";

const SECTIONS: Array<{
  id: HealthScope;
  label: string;
  icon: typeof Activity;
}> = [
  { id: "allta", label: "ALLTA Services Health", icon: Activity },
  { id: "astra", label: "Astra Services Health", icon: ShieldCheck },
];

interface HealthRow {
  id: string;
  service: string;
  status: "ok" | "fail";
  checked_at: string | null;
  latency: string;
  error: string | null;
}

function scopeFromParam(value: string | undefined): HealthScope {
  return value === "astra" ? "astra" : "allta";
}

function statusFromStates(...states: HealthState[]): "ok" | "fail" {
  return states.every((s) => s === "up") ? "ok" : "fail";
}

function statusBadge(status: "ok" | "fail") {
  return (
    <Badge kind={status === "ok" ? "ok" : "danger"}>
      {status}
    </Badge>
  );
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

function alltaRows(services: ServiceHealth[] | null): HealthRow[] {
  return (services ?? []).map((s) => ({
    id: s.id,
    service: s.label,
    status: statusFromStates(s.health, s.ready),
    checked_at: s.checked_at,
    latency: fmtLatency(s.health_latency_ms, s.ready_latency_ms),
    error: [s.health_error, s.ready_error].filter(Boolean).join(" / ") || null,
  }));
}

function astraRows(): HealthRow[] {
  return [
    {
      id: "astra-api",
      service: "astra-api",
      status: "fail",
      checked_at: null,
      latency: "-",
      error: "Источник health пока не подключён",
    },
    {
      id: "astra-db",
      service: "astra-db",
      status: "fail",
      checked_at: null,
      latency: "-",
      error: "Источник health пока не подключён",
    },
  ];
}

export function ServicesHealth() {
  const params = useParams();
  const activeId = scopeFromParam(params.scope);
  const [services, setServices] = useState<ServiceHealth[] | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      const next = await checkAllServices();
      if (!cancelled) setServices(next);
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const active = useMemo(
    () => SECTIONS.find((s) => s.id === activeId) ?? SECTIONS[0],
    [activeId],
  );
  const rows = activeId === "allta" ? alltaRows(services) : astraRows();
  const ActiveIcon = active.icon;

  const middle = (
    <aside className="surface border-r border-token min-h-0 flex flex-col">
      <div className="p-3 border-b border-token">
        <div className="text-sm font-medium">Здоровье служб</div>
      </div>
      <div className="p-2 flex flex-col gap-1">
        {SECTIONS.map((s) => {
          const Icon = s.icon;
          const isActive = s.id === activeId;
          return (
            <Link
              key={s.id}
              to={`/health/${s.id}`}
              className={`chip ${isActive ? "active" : ""}`}
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span className="text-sm truncate">{s.label}</span>
            </Link>
          );
        })}
      </div>
    </aside>
  );

  return (
    <Shell breadcrumb={`health / ${active.label}`} middle={middle}>
      <main className="flex-1 min-w-0 overflow-auto">
        <div className="p-5">
          <section className="surface border border-token rounded">
            <div className="p-4 border-b border-token flex items-center gap-3">
              <div className="h-9 w-9 rounded bg-accent/10 border border-token flex items-center justify-center">
                <ActiveIcon className="w-5 h-5 text-accent" />
              </div>
              <div className="min-w-0">
                <h1 className="text-lg font-semibold truncate">{active.label}</h1>
                <div className="text-xs text-dim">service health</div>
              </div>
            </div>
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
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-4 py-6 text-dim">
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
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </main>
    </Shell>
  );
}
