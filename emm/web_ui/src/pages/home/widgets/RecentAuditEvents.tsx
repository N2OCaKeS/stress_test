/**
 * Компактный блок «Последние события» для home-дашбордов.
 *
 * Тянет несколько свежих audit-событий из loging_service тем же live-слоем,
 * что и `/log` (`@/api/loging/events`). Рендерится только если у персоны есть
 * доступ к чтению журнала (`hasAuditLogAccess`) — вызывающий обязан проверить
 * это снаружи, но компонент дополнительно гейтит сам себя, чтобы не дёргать
 * backend для роли без доступа (иначе словит 403).
 */
import { Link } from "react-router-dom";
import { AlertCircle } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { hasAuditLogAccess } from "@/lib/rbac";
import { formatMskTime } from "@/lib/datetime";
import { listEvents } from "@/api/loging/events";
import type { EventListResponse } from "@/api/loging/types";
import { Button } from "@/components/ui/Button";

const PREVIEW_COUNT = 8;

/** CSS-класс severity-бейджа; DEBUG/TRACE подкрашиваем как INFO. */
function sevClass(severity: string): string {
  if (severity === "DEBUG" || severity === "TRACE") return "sev-INFO";
  return `sev-${severity}`;
}

const SEV_LABEL: Record<string, string> = {
  CRITICAL: "CRIT",
  ERROR: "ERR",
  WARNING: "WARN",
  INFO: "INFO",
  DEBUG: "DBG",
  TRACE: "TRC",
};

export function RecentAuditEvents() {
  const { persona } = usePersona();
  const canAudit = hasAuditLogAccess(persona);

  const eventsQ = useQuery<EventListResponse>(
    () => listEvents({ limit: PREVIEW_COUNT }),
    [],
    { enabled: canAudit },
  );

  if (!canAudit) return null;

  const items = eventsQ.data?.items ?? [];

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">Последние события</h3>
        <Link to="/log" className="text-xs text-accent">
          Открыть лог →
        </Link>
      </div>
      {eventsQ.loading ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : eventsQ.error ? (
        <div className="alert alert-danger flex items-start gap-2 text-xs">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1">
            <div>{apiErrMsg(eventsQ.error, "Журнал не загрузился")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => eventsQ.refetch()}>
              Повторить
            </Button>
          </div>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-card text-xs">За последнее время событий нет.</div>
      ) : (
        <div className="text-sm">
          {items.map((ev) => (
            <div key={ev.id} className="activity-row">
              <span className="text-xs text-dim mono">
                {formatMskTime(ev.timestamp)}
              </span>
              <div className="min-w-0">
                <div className="truncate">
                  <span className="mono">{ev.action}</span>{" "}
                  <span className="text-dim">
                    · {ev.username ?? ev.actor_id ?? ev.actor_type}
                  </span>
                </div>
                <div className="text-[11px] text-dim mono truncate">
                  {ev.service}
                  {ev.request_id ? ` · ${ev.request_id}` : ""}
                </div>
              </div>
              <span className={`sev ${sevClass(ev.severity)}`}>
                {SEV_LABEL[ev.severity] ?? ev.severity}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
