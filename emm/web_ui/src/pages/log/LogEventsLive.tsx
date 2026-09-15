/**
 * Live-просмотр журнала аудита loging_service.
 *
 * Список событий слева (фильтры severity / service / status / action,
 * actor / target / department / request_id / диапазон времени +
 * пагинация через `has_more`), карточка выбранного события справа.
 * Все вызовы идут в `loging_service` через `@/api/loging/events`.
 *
 * Доступ к чтению — `loging_admin` / `loging_reader`. Reader видит тот же
 * read-канал (управление правилами/retention для него закрыто другими
 * страницами).
 */
import { useMemo, useState } from "react";
import {
  Search,
  AlertCircle,
  AlertTriangle,
  User,
  Filter,
  Cog,
  Download,
  X,
  SlidersHorizontal,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Dropdown } from "@/components/ui/Dropdown";
import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { hasAuditLogAccess } from "@/lib/rbac";
import { formatMsk, formatMskTime } from "@/lib/datetime";
import { exportEvents, getEventStats, listEvents, listEventFilterOptions } from "@/api/loging/events";
import { listServices } from "@/api/loging/services";
import { useDeptLabelOpt, useLabelMaps } from "@/lib/labels";
import type {
  EventDetail,
  EventStatsQuery,
  EventStatsResponse,
  EventStatus,
  ListEventsQuery,
  Severity,
} from "@/api/loging/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

const PAGE_SIZE = 100;

/**
 * Набор фильтров журнала, который страница держит в одном объекте состояния.
 * `action` редактируется в строке поиска сверху, остальное — в панели фильтров.
 * Все поля — строки (значение пустого `<input>`/`<select>`); в query они
 * превращаются в `undefined`, чтобы пустой фильтр не уходил на backend.
 */
interface LogFilters {
  severity: string;
  service: string;
  status: string;
  action: string;
  actorId: string;
  targetId: string;
  departmentId: string;
  requestId: string;
  /** `datetime-local` (`YYYY-MM-DDTHH:mm`), трактуется как MSK. */
  fromTime: string;
  toTime: string;
}

const EMPTY_FILTERS: LogFilters = {
  severity: "",
  service: "",
  status: "",
  action: "",
  actorId: "",
  targetId: "",
  departmentId: "",
  requestId: "",
  fromTime: "",
  toTime: "",
};

/** Сдвиг MSK относительно UTC в минутах (UTC+3, без переходов). */
const MSK_OFFSET_MIN = 3 * 60;

/**
 * Значение `<input type="datetime-local">` (наивное `YYYY-MM-DDTHH:mm` без
 * зоны) пользователь задаёт в московском времени — весь журнал тоже
 * показывается в MSK. Переводим его в UTC ISO, вычитая смещение MSK, чтобы
 * диапазон на backend'е (UTC) совпадал с тем, что видно в списке.
 */
function mskLocalToUtcIso(local: string): string | undefined {
  if (local.length < 16) return undefined;
  const asUtc = new Date(`${local}:00Z`).getTime();
  if (Number.isNaN(asUtc)) return undefined;
  return new Date(asUtc - MSK_OFFSET_MIN * 60_000).toISOString();
}

const SEVERITIES: Severity[] = [
  "CRITICAL",
  "ERROR",
  "WARNING",
  "INFO",
  "DEBUG",
  "TRACE",
];

const STATUSES: EventStatus[] = ["success", "failure", "denied", "warning"];

/**
 * Известные сервисы-источники платформы. `GET /events/services` отдаёт только
 * те, что уже писали события (GROUP BY service), поэтому свежий/тихий сервис в
 * дропдауне не появится. Мерджим эти имена с выдачей backend'а, чтобы по
 * `server_service` / `server_worker` можно было отфильтровать заранее, не дожидаясь
 * первого их события.
 */
const KNOWN_SERVICES = [
  "auth_service",
  "loging_service",
  "server_service",
  "server_worker",
  "secret_service",
];

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

/** Порядок и подписи severity-строк в распределении. */
const SEV_ORDER: Severity[] = [
  "CRITICAL",
  "ERROR",
  "WARNING",
  "INFO",
  "DEBUG",
  "TRACE",
];

const numFmt = new Intl.NumberFormat("ru-RU");

export function LogEventsLive() {
  const { persona } = usePersona();
  const canAudit = hasAuditLogAccess(persona);

  const [filters, setFilters] = useState<LogFilters>(EMPTY_FILTERS);
  const [showFilters, setShowFilters] = useState(false);
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { depts } = useLabelMaps();
  const namesQ = useQuery(async () => {
    const [department, actor, target] = await Promise.all(["department", "actor", "target"].map((kind) => listEventFilterOptions(kind as "department" | "actor" | "target")));
    return { department, actor, target };
  }, [], { enabled: canAudit });

  // Обновление любого фильтра сбрасывает пагинацию — иначе текущий offset мог
  // бы указывать за пределы новой (более узкой) выборки.
  function setFilter<K extends keyof LogFilters>(key: K, value: LogFilters[K]) {
    setPage(0);
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  function resetFilters() {
    setPage(0);
    setFilters(EMPTY_FILTERS);
  }

  const query: ListEventsQuery = useMemo(
    () => ({
      severity: (filters.severity || undefined) as Severity | undefined,
      service: filters.service || undefined,
      status: (filters.status || undefined) as EventStatus | undefined,
      action: filters.action.trim() || undefined,
      actor_id: filters.actorId.trim() || undefined,
      target_id: filters.targetId.trim() || undefined,
      department_id: filters.departmentId.trim() || undefined,
      request_id: filters.requestId.trim() || undefined,
      from_time: mskLocalToUtcIso(filters.fromTime),
      to_time: mskLocalToUtcIso(filters.toTime),
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [filters, page],
  );

  // Та же выборка для сводки/экспорта (без пагинации). Если задан явный
  // диапазон времени — он перекрывает дефолтное окно 24ч на backend'е; иначе
  // окно остаётся дефолтным и stats считается за последние сутки.
  const statsQuery: EventStatsQuery = useMemo(() => {
    const { limit: _l, offset: _o, ...rest } = query;
    return rest;
  }, [query]);

  const eventsQ = useQuery(() => listEvents(query), [query]);
  const servicesQ = useQuery(() => listServices(), []);

  const items = eventsQ.data?.items ?? [];
  const hasMore = eventsQ.data?.has_more ?? false;
  const selected = items.find((e) => e.id === selectedId) ?? null;

  // Сколько фильтров (кроме поиска по action) сейчас задано — для бейджа на
  // кнопке панели, чтобы было видно «фильтры активны» при свёрнутой панели.
  const activeExtra = [
    filters.severity,
    filters.service,
    filters.status,
    filters.actorId.trim(),
    filters.targetId.trim(),
    filters.departmentId.trim(),
    filters.requestId.trim(),
    filters.fromTime,
    filters.toTime,
  ].filter(Boolean).length;
  const anyActive = activeExtra > 0 || filters.action.trim().length > 0;

  const deptOptions = useMemo(() => {
    const names = new Map((namesQ.data?.department ?? []).map((option) => [option.value, option.label]));
    depts.forEach((name, id) => names.set(id, name));
    return [...names].map(([value, label]) => ({ value, label }));
  }, [depts, namesQ.data]);

  // Имена сервисов из выдачи backend'а (только писавшие события) + известные
  // платформенные сервисы, чтобы по тихому сервису тоже можно было фильтровать.
  const serviceOptions = useMemo(() => {
    const set = new Set<string>(KNOWN_SERVICES);
    for (const s of servicesQ.data?.items ?? []) set.add(s.service);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [servicesQ.data]);

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder="Фильтр по action (точное совпадение)…"
            value={filters.action}
            onChange={(e) => setFilter("action", e.target.value)}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={`flex items-center gap-1 ${showFilters ? "text-accent" : ""}`}
            onClick={() => setShowFilters((v) => !v)}
            title="Фильтры"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            {activeExtra > 0 && (
              <Badge className="text-[10px]">{activeExtra}</Badge>
            )}
          </Button>
        </div>

        {showFilters && (
          <div className="flex flex-col gap-1.5 text-[11px] text-dim pt-1">
            <div className="grid grid-cols-3 gap-1">
              <Dropdown
                mode="single"
                options={SEVERITIES.map((s) => ({ value: s, label: s }))}
                value={filters.severity}
                onChange={(v) => setFilter("severity", v)}
                placeholder="важность"
              />
              <Dropdown
                mode="single"
                searchable
                options={serviceOptions.map((s) => ({ value: s, label: s }))}
                value={filters.service}
                onChange={(v) => setFilter("service", v)}
                placeholder="сервис"
              />
              <Dropdown
                mode="single"
                options={STATUSES.map((s) => ({ value: s, label: s }))}
                value={filters.status}
                onChange={(v) => setFilter("status", v)}
                placeholder="статус"
              />
            </div>

            <Dropdown mode="single" label="Отдел" placeholder="Любой" options={deptOptions}
              value={filters.departmentId} onChange={(v) => setFilter("departmentId", v)} />
            <Dropdown mode="single" label="Инициатор" placeholder="Любой" options={namesQ.data?.actor ?? []}
              value={filters.actorId} onChange={(v) => setFilter("actorId", v)} />
            <Dropdown mode="single" label="Объект" placeholder="Любой" options={namesQ.data?.target ?? []}
              value={filters.targetId} onChange={(v) => setFilter("targetId", v)} />
            {!!namesQ.error && <span role="alert">{apiErrMsg(namesQ.error, "Не удалось загрузить имена из журнала")}</span>}
            <span>Списки содержат имена, сохранённые в событиях аудита.</span>
            <input
              className="surface-2 border border-token rounded px-1.5 py-0.5 w-full"
              placeholder="request_id (трассировка)"
              value={filters.requestId}
              onChange={(e) => setFilter("requestId", e.target.value)}
              title="Фильтр по request_id"
            />

            <div className="grid grid-cols-[auto_1fr] items-center gap-1">
              <span className="text-dim">с (MSK)</span>
              <input
                type="datetime-local"
                className="surface-2 border border-token rounded px-1 py-0.5 w-full"
                value={filters.fromTime}
                onChange={(e) => setFilter("fromTime", e.target.value)}
                title="Начало диапазона (московское время)"
              />
              <span className="text-dim">по (MSK)</span>
              <input
                type="datetime-local"
                className="surface-2 border border-token rounded px-1 py-0.5 w-full"
                value={filters.toTime}
                onChange={(e) => setFilter("toTime", e.target.value)}
                title="Конец диапазона (московское время)"
              />
            </div>

            <Button variant="ghost" size="sm"
              type="button"
              className="flex items-center justify-center gap-1 mt-0.5"
              onClick={resetFilters}
              disabled={!anyActive}
            >
              <X className="w-3.5 h-3.5" />
              Сбросить фильтры
            </Button>
          </div>
        )}
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
              <Button variant="ghost"
                className="mt-2"
                onClick={() => eventsQ.refetch()}
              >
                Повторить
              </Button>
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
            <div className="ev-time">{formatMskTime(row.timestamp)}</div>
            <div className="min-w-0">
              <div className="ev-action truncate">{row.action}</div>
              <div className="ev-meta truncate">
                {row.service} ·{" "}
                {row.username ? (
                  <>
                    {row.username}
                    {row.actor_id && (
                      <span className="mono text-dim"> · {row.actor_id}</span>
                    )}
                  </>
                ) : (
                  <span className="mono">{row.actor_id ?? row.actor_type}</span>
                )}
              </div>
            </div>
            <span className={`sev ${sevClass(row.severity)}`}>
              {SEV_LABEL[row.severity] ?? row.severity}
            </span>
          </button>
        ))}
      </div>

      <div className="border-t border-token px-3 py-2 shrink-0 flex items-center justify-between text-xs text-dim">
        <Button variant="ghost"
          disabled={page === 0 || eventsQ.loading}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          ←
        </Button>
        <span>стр. {page + 1}</span>
        <Button variant="ghost"
          disabled={!hasMore || eventsQ.loading}
          onClick={() => setPage((p) => p + 1)}
        >
          →
        </Button>
      </div>
    </aside>
  );

  return (
    <Shell breadcrumb="loging_service / events" middle={aside}>
      {selected ? (
        <EventDetailPane event={selected} />
      ) : canAudit ? (
        <StatsPane statsQuery={statsQuery} hasFilters={anyActive} />
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

/**
 * Дефолтная рабочая зона для loging-роли, пока событие не выбрано: сводка
 * `GET /events/stats` (severity-распределение, by_service, by_status) плюс
 * кнопка экспорта CSV. Считается по тем же фильтрам, что и список слева:
 * без явного диапазона времени окно дефолтится на 24ч (backend), с ним —
 * берётся заданный `from_time`/`to_time`. Гейтится снаружи по
 * `hasAuditLogAccess`, так что 403-на-загрузке здесь не возникает.
 */
function StatsPane({
  statsQuery,
  hasFilters,
}: {
  statsQuery: EventStatsQuery;
  hasFilters: boolean;
}) {
  const hasRange = Boolean(statsQuery.from_time || statsQuery.to_time);
  const statsQ = useQuery<EventStatsResponse>(
    () => getEventStats(hasRange ? statsQuery : { ...statsQuery, window_hours: 24 }),
    [statsQuery, hasRange],
  );
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "warn" | "err"; text: string } | null>(
    null,
  );

  async function onExport() {
    setExporting(true);
    setNotice(null);
    try {
      const res = await exportEvents(
        hasRange ? statsQuery : { ...statsQuery, window_hours: 24 },
      );
      setNotice(
        res.truncated
          ? {
              kind: "warn",
              text: `Экспорт ${res.filename} скачан, но усечён до 50 000 строк — сузьте окно или фильтр.`,
            }
          : { kind: "ok", text: `Экспорт ${res.filename} скачан.` },
      );
    } catch (e) {
      setNotice({ kind: "err", text: apiErrMsg(e, "Экспорт не удался") });
    } finally {
      setExporting(false);
    }
  }

  const stats = statsQ.data;

  return (
    <section className="flex-1 min-w-0 overflow-y-auto p-5">
      <div className="w-full space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h1 className="text-xl font-semibold">
              Сводка аудита{hasRange ? "" : " · 24ч"}
              {hasFilters && (
                <span className="text-xs text-dim font-normal ml-2">
                  (по фильтрам)
                </span>
              )}
            </h1>
            {stats && (
              <div className="text-xs text-dim mt-1">
                {formatMsk(stats.from_time)} → {formatMsk(stats.to_time)}
              </div>
            )}
          </div>
          <Button size="sm"
            type="button"
            className="flex items-center gap-1"
            onClick={onExport}
            disabled={exporting}
          >
            <Download className="w-3.5 h-3.5" />
            {exporting ? "Экспорт…" : hasRange ? "Экспорт за период" : "Экспорт за 24ч"}
          </Button>
        </div>

        {notice && (
          <div
            className={
              notice.kind === "err"
                ? "alert alert-danger text-xs"
                : notice.kind === "warn"
                  ? "alert-warn text-xs"
                  : "alert alert-success text-xs"
            }
            role="status"
          >
            {notice.text}
          </div>
        )}

        {statsQ.loading && (
          <div className="empty-card text-xs text-center">Загрузка статистики…</div>
        )}

        {statsQ.error && (
          <div className="alert alert-danger flex items-start gap-2 text-xs">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1">
              <div>{apiErrMsg(statsQ.error, "Статистика не загрузилась")}</div>
              <Button variant="ghost" className="mt-2" onClick={() => statsQ.refetch()}>
                Повторить
              </Button>
            </div>
          </div>
        )}

        {!statsQ.loading && !statsQ.error && stats && (
          <StatsBody stats={stats} />
        )}
      </div>
    </section>
  );
}

function StatsBody({ stats }: { stats: EventStatsResponse }) {
  const sevRows = SEV_ORDER.map((s) => ({ sev: s, count: stats.by_severity[s] ?? 0 })).filter(
    (r) => r.count > 0,
  );
  const sevMax = Math.max(1, ...sevRows.map((r) => r.count));
  const services = Object.entries(stats.by_service).sort((a, b) => b[1] - a[1]);
  const success = stats.by_status.success ?? 0;
  const failure = stats.by_status.failure ?? 0;

  if (stats.total === 0) {
    return (
      <div className="empty-card text-xs text-center">
        За окно событий нет.
      </div>
    );
  }

  return (
    <>
      {stats.truncated && (
        <div className="alert-warn text-xs" role="status">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">
            Выборка усечена — счётчики ниже неполные. Сузьте окно или фильтр.
          </span>
        </div>
      )}

      <div className="card">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <div className="stat-label">events / 24h</div>
            <div className="stat-big">{numFmt.format(stats.total)}</div>
          </div>
          <div>
            <div className="stat-label">success</div>
            <div className="stat-big">{numFmt.format(success)}</div>
          </div>
          <div>
            <div className="stat-label">failure</div>
            <div className={`stat-big ${failure > 0 ? "text-danger" : ""}`}>
              {numFmt.format(failure)}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h3 className="font-semibold mb-3">Распределение по важности</h3>
        {sevRows.length === 0 ? (
          <div className="text-xs text-dim">Нет данных по важности.</div>
        ) : (
          sevRows.map((r) => (
            <div key={r.sev} className="sev-bar">
              <div>
                <span className={`sev ${sevClass(r.sev)}`}>
                  {SEV_LABEL[r.sev] ?? r.sev}
                </span>
              </div>
              <div className="bar">
                <span
                  style={{
                    width: `${Math.round((r.count / sevMax) * 100)}%`,
                    background:
                      r.sev === "CRITICAL" || r.sev === "ERROR"
                        ? "var(--danger)"
                        : r.sev === "WARNING"
                          ? "var(--warn)"
                          : "var(--accent)",
                  }}
                />
              </div>
              <div className="text-right mono">{numFmt.format(r.count)}</div>
            </div>
          ))
        )}
      </div>

      <div className="card">
        <h3 className="font-semibold mb-3">По сервисам</h3>
        {services.length === 0 ? (
          <div className="text-xs text-dim">Нет данных по сервисам.</div>
        ) : (
          <div className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
            {services.map(([svc, count]) => (
              <div key={svc} className="contents">
                <div className="mono text-dim truncate">{svc}</div>
                <div className="text-right mono">{numFmt.format(count)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

function EventDetailPane({ event }: { event: EventDetail }) {
  // Имя отдела для старых записей без `department_name`: резолвим по id из
  // карты отделов; если карта недоступна (роль без списка) — вернётся сам id.
  const deptResolved = useDeptLabelOpt(event.department_id);
  const deptName = event.department_name ?? deptResolved;
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
            <Badge>{event.status}</Badge>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            {event.request_id && (
              <span className="mono">{event.request_id}</span>
            )}
            <span className="mono">{formatMsk(event.timestamp)}</span>
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
              Инициатор
            </div>
          </div>
          <div className="text-sm">
            <DetailRowNameId
              label="Инициатор"
              name={event.username}
              id={event.actor_id}
            />
            <DetailRow label="Тип" value={event.actor_type} />
            <DetailRowNameId
              label="Отдел"
              name={deptName}
              id={event.department_id}
            />
            <DetailRow label="Разрешено" value={event.allowed ? "да" : "нет"} />
            <DetailRow label="IP инициатора" value={event.actor_ip} mono />
            <DetailRow label="User-Agent" value={event.user_agent} mono />
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4">
          <div className="flex items-center gap-2 mb-3">
            <Cog className="w-4 h-4 text-dim" />
            <div className="text-xs uppercase tracking-wider text-dim">
              Объект / Сервис
            </div>
          </div>
          <div className="text-sm">
            <DetailRow label="Сервис" value={event.service} />
            <DetailRow label="Действие" value={event.action} mono />
            <DetailRow label="Статус" value={event.status} />
            <DetailRow label="Важность" value={event.severity} />
            <DetailRow label="Тип объекта" value={event.target_type} />
            <DetailRow label="ID объекта" value={event.target_id} mono />
            <DetailRow label="Когда (MSK)" value={formatMsk(event.timestamp)} mono />
            <DetailRow label="Получено" value={formatMsk(event.received_at)} mono />
            <DetailRow label="ID запроса" value={event.request_id} mono />
          </div>
        </div>

        <div className="surface border border-token rounded-lg p-4 col-span-2">
          <div className="flex items-center gap-2 mb-3">
            <Filter className="w-4 h-4 text-dim" />
            <div className="text-xs uppercase tracking-wider text-dim">
              Детали JSON
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

/**
 * Строка детали для сущности с парой имя+id (актор, отдел): имя — обычным
 * шрифтом, сырой id — моноширинно ниже. Если имя не резолвится (равно id или
 * пусто), показываем только id моноширинно, чтобы не дублировать одно и то же.
 */
function DetailRowNameId({
  label,
  name,
  id,
}: {
  label: string;
  name: string | null | undefined;
  id: string | null | undefined;
}) {
  if (!id && !name) {
    return (
      <div className="stat-row">
        <span className="text-dim">{label}</span>
        <span className="text-dim">—</span>
      </div>
    );
  }
  const hasName = Boolean(name) && name !== id;
  return (
    <div className="stat-row items-start">
      <span className="text-dim">{label}</span>
      <span className="text-right flex flex-col items-end">
        {hasName && <span>{name}</span>}
        {id && (
          <span className="mono text-xs text-dim" title={id}>
            {id}
          </span>
        )}
      </span>
    </div>
  );
}
