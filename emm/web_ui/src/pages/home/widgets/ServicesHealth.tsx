/**
 * Блок состояния backend-сервисов для home-дашбордов.
 *
 * Пингует health/ready всех четырёх сервисов через `@/api/health` с умеренным
 * polling'ом (по умолчанию 20с). Каждый сервис показывает два индикатора:
 * liveness (`/health`) и readiness (`/ready`). Деградирует мягко — лежащий
 * сервис рисуется красным, таймаут — серым «нет данных», блок не падает.
 */
import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import {
  checkAllServices,
  type HealthState,
  type ServiceHealth,
} from "@/api/health";

const POLL_INTERVAL_MS = 20_000;

function dotClass(state: HealthState): string {
  if (state === "up") return "text-ok";
  if (state === "down") return "text-danger";
  return "text-dim";
}

function dotLabel(state: HealthState): string {
  if (state === "up") return "up";
  if (state === "down") return "down";
  return "нет данных";
}

export function ServicesHealth() {
  const [services, setServices] = useState<ServiceHealth[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      const res = await checkAllServices();
      if (!cancelled) {
        setServices(res);
        setLoading(false);
      }
    }

    poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">Состояние сервисов</h3>
        <Activity className="w-4 h-4 text-dim" />
      </div>
      {loading && !services ? (
        <div className="text-xs text-dim">Опрос health/ready…</div>
      ) : (
        <div className="space-y-1.5 text-sm">
          {(services ?? []).map((s) => (
            <div
              key={s.id}
              className="flex items-center gap-2 p-1.5 surface-2 rounded"
            >
              <span className="font-medium flex-1 mono">{s.label}</span>
              <span className={`text-[11px] ${dotClass(s.health)}`}>
                ● health: {dotLabel(s.health)}
              </span>
              <span className={`text-[11px] ${dotClass(s.ready)}`}>
                ● ready: {dotLabel(s.ready)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
