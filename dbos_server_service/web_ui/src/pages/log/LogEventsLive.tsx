/**
 * Live-просмотр журнала аудита loging_service.
 *
 * Список событий слева (фильтры severity / service / status / action +
 * пагинация через `has_more`), карточка выбранного события справа.
 * Все вызовы идут в `loging_service` через `@/api/loging/events`.
 *
 * Доступ к чтению — `loging_admin` / `loging_reader`. Reader видит тот же
 * read-канал (управление правилами/retention для него закрыто другими
 * страницами).
 */
import { useMemo, useState } from "react";
import { Search, AlertCircle, ShieldAlert, User, Filter, Cog } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError } from "@/api/client";
import { listEvents } from "@/api/loging/events";
import { listServices } from "@/api/loging/services";
import type {
  EventDetail,
  EventStatus,
  ListEventsQuery,
  Severity,
} from "@/api/loging/types";

const PAGE_SIZE = 100;

const SEVERITIES: Severity[] = [
  "CRITICAL",
  "ERROR",
  "WARNING",
  "INFO",
  "DEBUG",
  "TRACE",
];

const STATUSES: EventStatus[] = ["success", "failure", "denied", "warning"];

/** Короткая метка severity для бейджа в списке. */
const SEV_LABEL: Record<string, string> = {
  CRITICAL: "CRIT",
  ERROR: "ERR",
  WARNING: "WARN",
  INFO: "INFO",
  DEBUG: "DBG",
  TRACE: "TRC",
};

/** Маппинг на CSS-класс `.sev-*`; для DEBUG/TRACE подложку даёт INFO-стиль. */
function sevClass(severity: string): string {
  if (severity === "DEBUG" || severity === "TRACE") return "sev-INFO";
  return `sev-${severity}`;
}

function apiErrMsg(e: unknown, fallback = "Ошибка"): string {
  if (e instanceof ApiError) return `${e.errorCode}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return fallback;
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").replace("Z", "").slice(0, 19);
}

export function LogEventsLive() {
  const { persona } = usePersona();
  const isReader = persona.platform_role === "logging_reader";

  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<string>("");
  const [service, setService] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const query: ListEventsQuery = useMemo(
    () => ({
      severity: (severity || undefined) as Severity | undefined,
      service: service || undefined,
      status: (status || undefined) as EventStatus | undefined,
      action: search.trim() || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [severity, service, status, search, page],
  );

  const eventsQ = useQuery(() => listEvents(query), [
    severity,
    service,
    status,
    search,
    page,
  ]);
  const servicesQ = useQuery(() => listServices(), []);

  const items = eventsQ.data?.items ?? [];
  const hasMore = eventsQ.data?.has_more ?? false;
  const selected = items.find((e) => e.id === selectedId) ?? null;

  function resetPageAnd(setter: (v: string) => void) {
    return (v: string) => {
      setPage(0);
      setter(v);
    };
  }

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder="Фильтр по action (точное совпадение)…"
            value={search}
            onChange={(e) => {
              setPage(0);
              setSearch(e.target.value);
            }}
          />
        </div>
        <div className="grid grid-cols-3 gap-1 text-[11px] text-dim">
          <select
            className="surface-2 border border-token rounded px-1 py-0.5"
            value={severity}
            onChange={(e) => resetPageAnd(setSeverity)(e.target.value)}
            title="Severity"
          >
            <option value="">severity</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            className="surface-2 border border-token rounded px-1 py-0.5"
            value={service}
            onChange={(e) => resetPageAnd(setService)(e.target.value)}
            title="Сервис-источник"
          >
            <option value="">сервис</option>
            {(servicesQ.data?.items ?? []).map((s) => (
              <option key={s.service} value={s.service}>
                {s.service}
              </option>
            ))}
          </select>
          <select
            className="surface-2 border border-token rounded px-1 py-0.5"
            value={status}
            onChange={(e) => resetPageAnd(setStatus)(e.target.value)}
            title="Статус"
          >
            <option value="">статус</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {eventsQ.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {eventsQ.error && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(eventsQ.error, "Журнал не загрузился")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => eventsQ.refetch()}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
        {!eventsQ.loading && !eventsQ.error && items.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            Событий нет.
          </div>
        )}
        {items.map((row) => (
          <button
            key={row.id}
            onClick={() => setSelectedId(row.id)}
            className={`ev-row text-left w-full ${
              selectedId === row.id ? "active" : ""
            }`}
          >
            <div className="ev-time">{fmtTime(row.timestamp).slice(11)}</div>
            <div className="min-w-0">
              <div className="ev-action truncate">{row.action}</div>
              <div className="ev-meta truncate">
                {row.service} · {row.username ?? row.actor_id ?? row.actor_type}
              </div>
            </div>
            <span className={`sev ${sevClass(row.severity)}`}>
              {SEV_LABEL[row.severity] ?? row.severity}
            </span>
          </button>
        ))}
      </div>

      <div className="border-t border-token px-3 py-2 shrink-0 flex items-center justify-between text-xs text-dim">
        <button
          className="btn btn-ghost"
          disabled={page === 0 || eventsQ.loading}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          ←
        </button>
        <span>стр. {page + 1}</span>
        <button
          className="btn btn-ghost"
          disabled={!hasMore || eventsQ.loading}
          onClick={() => setPage((p) => p + 1)}
        >
          →
        </button>
      </div>
    </aside>
  );

  return (
    <Shell breadcrumb="loging_service / events" middle={aside}>
      {isReader && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · loging_reader.</b> Доступен только просмотр событий.
          </span>
        </div>
      )}
      {selected ? (
        <EventDetailPane event={selected} />
      ) : (
        <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
          <div className="empty-card max-w-md text-center">
            <Filter className="w-10 h-10 mx-auto text-dim mb-3" />
            <div className="text-sm text-dim">
              Выберите событие слева для просмотра деталей.
            </div>
          </div>
        </section>
      )}
    </Shell>
  );
}

function EventDetailPane({ event }: { event: EventDetail }) {
  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">
              {event.action}
            </h1>
            <span className={`sev ${sevClass(event.severity)}`}>
              {event.severity}
            </span>
            <span className="badge">{event.status}</span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            {event.request_id && (
              <span className="mono">{event.request_id}</span>
            )}
            <span className="mono">{fmtTime(event.timestamp)} UTC</span>
            <span>·</span>
            <span className="mono">{event.id}</span>
          </div>
        </div>
      </div>

      <div className="scroll-block p-5 grid grid-cols-2 gap-5 content-start">
        <div className="surface border border-token rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <User className="w-4 h-4 text-accent" />
            <div className="text-xs uppercase tracking-wider text-dim">
              Actor
            </div>
          </div>
          <div className="text-sm">
            <DetailRow label="Username" value={event.username} />
            <DetailRow label="Actor ID" value={event.actor_id} mono />
            <DetailRow label="Type" value={event.actor_type} />
            <DetailRow label="Department" value={event.department_id} mono />
            <DetailRow label="Allowed" value={event.allowed ? "yes" : "no"} />
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Cog className="w-4 h-4 text-dim" />
            <div className="text-xs uppercase tracking-wider text-dim">
              Target / Service
            </div>
          </div>
          <div className="text-sm">
            <DetailRow label="Service" value={event.service} />
            <DetailRow label="Target type" value={event.target_type} />
            <DetailRow label="Target ID" value={event.target_id} mono />
            <DetailRow label="Received" value={fmtTime(event.received_at)} mono />
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="flex items-center gap-2 mb-3">
            <Filter className="w-4 h-4 text-dim" />
            <div className="text-xs uppercase tracking-wider text-dim">
              Details JSON
            </div>
          </div>
          <pre className="json-block mono whitespace-pre-wrap break-all">
            {JSON.stringify(event.details ?? {}, null, 2)}
          </pre>
        </div>
      </div>
    </section>
  );
}

function DetailRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
}) {
  return (
    <div className="stat-row">
      <span className="text-dim">{label}</span>
      {value ? (
        <span className={mono ? "mono text-xs" : ""}>{value}</span>
      ) : (
        <span className="text-dim">—</span>
      )}
    </div>
  );
}
