/** Одиночные запуски и логи реальных попыток вне кампаний. */
import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, Bug, ChevronDown, ChevronUp, Play, Search } from "lucide-react";
import { formatElapsedHMS, formatMsk } from "@/lib/datetime";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { listQueueItems, retryQueueItem } from "@/api/testing/queueItems";
import {
  getStatisticsCategories,
  getStatisticsStatus,
  triggerStatisticsRecalc,
} from "@/api/testing/statistics";
import { listOsVersions } from "@/api/server/osVersions";
import { listTestDefinitions } from "@/api/testing/testDefinitions";
import { listTestStands, getTestStand } from "@/api/testing/testStands";
import { AttemptLogWorkzone } from "./AttemptLogWorkzone";
import { StandaloneLaunchModal } from "./StandaloneLaunchModal";
import { type BadgeKind } from "./_shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

export type AdhocStatus = "queued" | "running" | "done" | "failed";
export type AdhocMode = "orel" | "smolensk";

export interface AdhocRun {
  id: string;
  testCode: string;
  standName: string;
  standId: string;
  testName: string;
  debugMode: boolean;
  rc: string;
  kernel: string;
  error: string | null;
  canRetry: boolean;
  logStatus?: string;
  finishedAt: string | null;
  mode: AdhocMode;
  status: AdhocStatus;
  startedAt: string;
  createdAt: string;
}

const ADHOC_STATUS_META: Record<AdhocStatus, { label: string; badge: BadgeKind }> = {
  queued: { label: "В очереди", badge: "warn" },
  running: { label: "Выполняется", badge: "accent" },
  done: { label: "Успешно", badge: "ok" },
  failed: { label: "Провален", badge: "danger" },
};

const STATUS_FILTER_OPTIONS: (AdhocStatus | "all")[] = ["all", "queued", "running", "done", "failed"];

/** Тикающий `Date.now()` раз в секунду — источник для realtime-элапсед-таймера, без опроса бэкенда. */
function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

// ── состояние средней панели, общее для AdhocMiddlePanel и AdhocWorkzone ────

export interface AdhocState {
  runs: AdhocRun[];
  loading: boolean;
  error: unknown;
  refresh: () => void;
  total: number;
  offset: number;
  setOffset: (offset: number) => void;
  fetching: boolean;
  search: string;
  setSearch: (v: string) => void;
  statusFilter: AdhocStatus | "all";
  setStatusFilter: (v: AdhocStatus | "all") => void;
  sortDir: "asc" | "desc";
  toggleSort: () => void;
  selectedId: string;
  setSelectedId: (id: string) => void;
  selectedRun: AdhocRun | null;
}

export function useAdhocState(enabled = true): AdhocState {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<AdhocStatus | "all">("all");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedId, setSelectedId] = useState<string>("");

  const [offset, setOffset] = useState(0);
  const queueQ = useQuery(() => listQueueItems({
    q: search, order: sortDir, offset, limit: 50,
    states: statusFilter === "all" ? undefined : statusFilter === "queued" ? ["queued", "preparing", "ready"] : [statusFilter === "done" ? "succeeded" : statusFilter],
  }), [search, sortDir, statusFilter, offset], { enabled });
  const versionsQ = useQuery(() => listOsVersions({ limit: 500 }), [], { enabled });
  const testsQ = useQuery(() => listTestDefinitions({ limit: 500 }), [], { enabled });
  const standsQ = useQuery(async () => {
    const page = await listTestStands({ limit: 500 });
    return Promise.all(page.items.map((stand) => getTestStand(stand.id)));
  }, [], { enabled });
  const allRuns = useMemo<AdhocRun[]>(() => {
    const items = queueQ.data?.items ?? [];
    return items.map((item) => {
      const test = testsQ.data?.items.find((candidate) => candidate.id === item.test_id);
      const stand = standsQ.data?.find((candidate) => candidate.id === item.stand_id);
      const server = stand?.server as { display_name?: string; hostname?: string } | undefined;
      return {
        id: item.id, testCode: item.test_code ?? test?.code ?? item.test_id, testName: item.test_name ?? test?.full_name ?? item.test_id,
        standName: server?.display_name ?? server?.hostname ?? stand?.server_id ?? item.stand_id,
        standId: item.stand_id, mode: item.mode === "smolensk" ? "smolensk" : "orel",
        status: item.state === "succeeded" ? "done" : item.state === "failed" ? "failed" : item.state === "running" ? "running" : "queued",
        startedAt: item.started_at ?? item.created_at, createdAt: item.created_at, debugMode: item.debug_mode,
        rc: versionsQ.data?.items.find((version) => version.id === item.rc)?.name ?? item.rc ?? "—", kernel: item.kernel ?? "—", error: item.error,
        logStatus: item.log_status, finishedAt: item.finished_at,
        canRetry: ["succeeded", "failed"].includes(item.state) && item.is_current !== false,
      };
    });
  }, [queueQ.data, testsQ.data, standsQ.data, versionsQ.data]);
  const runs = allRuns;

  const hasActive = allRuns.some((run) => run.status === "queued" || run.status === "running");
  useEffect(() => {
    if (!enabled || !hasActive) return;
    const timer = setInterval(queueQ.refetch, 5000);
    return () => clearInterval(timer);
  }, [enabled, hasActive, queueQ.refetch]);

  const selectedRun = runs.find((r) => r.id === selectedId) ?? runs[0] ?? null;

  return {
    runs,
    total: queueQ.data?.total ?? 0, offset, setOffset, fetching: queueQ.isFetching,
    loading: queueQ.loading, error: queueQ.error, refresh: queueQ.refetch,
    search,
    setSearch: (value) => { setSearch(value); setOffset(0); },
    statusFilter,
    setStatusFilter: (value) => { setStatusFilter(value); setOffset(0); },
    sortDir,
    toggleSort: () => { setSortDir((d) => (d === "asc" ? "desc" : "asc")); setOffset(0); },
    selectedId,
    setSelectedId: (id) => { setSelectedId(id); },
    selectedRun,
  };
}

// ── средняя панель Shell: список разовых запусков ───────────────────────────

export function AdhocMiddlePanel({ state }: { state: AdhocState }) {
  const [launchOpen, setLaunchOpen] = useState(false);
  const toast = useToast();
  // Какой именно пересчёт сейчас запускаем: "" — полный, иначе ключ семейства.
  // null — ни один, кнопки активны.
  const [recalcPending, setRecalcPending] = useState<string | null>(null);
  // Восемь пер-категорийных кнопок легаси (девятая — «всё сразу» ниже). Список
  // приходит с бекенда, чтобы не разъезжаться с тем, что умеет внешний сервис.
  const categoriesQ = useQuery(() => getStatisticsCategories(), []);
  const categories = categoriesQ.data ?? [];
  // Живой индикатор фонового пересчёта (не только локальное "кнопка нажата,
  // ждём ответа сервера" — пересчёт может идти и по другой причине, тот же
  // индикатор, что и на левой панели, показывается только пока реально
  // выполняется, не последний известный итог.
  const [recalcRunning, setRecalcRunning] = useState(false);
  const [recalcCategory, setRecalcCategory] = useState<string | null>(null);
  const recalcCategoryLabel = recalcCategory
    ? categories.find((item) => item.key === recalcCategory)?.label ?? recalcCategory
    : null;
  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const status = await getStatisticsStatus();
        if (cancelled) return;
        setRecalcRunning(status.status === "running");
        setRecalcCategory(status.category);
      } catch {
        // best-effort индикатор — тихо оставляем предыдущее значение при сбое опроса
      }
    }
    poll();
    const timer = setInterval(poll, 15_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
  async function handleRecalc(category?: string, label?: string) {
    if (recalcPending !== null) return;
    setRecalcPending(category ?? "");
    try {
      await triggerStatisticsRecalc(category ? { category } : {});
      toast.success(
        `Пересчёт статистики${label ? ` «${label}»` : ""} запущен в фоне — статус смотрите слева на панели`,
      );
    } catch (error) {
      toast.error(apiErrMsg(error, "Не удалось запустить пересчёт статистики"));
    } finally {
      setRecalcPending(null);
    }
  }
  return (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${state.total} запускам…`}
            value={state.search}
            onChange={(e) => state.setSearch(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-1 mt-2 flex-wrap">
          {STATUS_FILTER_OPTIONS.map((s) => (
            <Button
              key={s}
              size="sm"
              type="button"
              variant={state.statusFilter === s ? "primary" : "default"}
              onClick={() => state.setStatusFilter(s)}
            >
              {s === "all" ? "Все" : ADHOC_STATUS_META[s].label}
            </Button>
          ))}
        </div>
        <Button
          size="sm"
          type="button"
          className="w-full mt-2 flex items-center justify-center gap-2"
          onClick={state.toggleSort}
        >
          {state.sortDir === "desc" ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          {state.sortDir === "desc" ? "Новые сверху" : "Старые сверху"}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {state.loading && <div className="p-3 text-xs">Загрузка запусков…</div>}
        {!!state.error && <div role="alert" className="p-3 text-xs text-danger">{apiErrMsg(state.error, "Не удалось загрузить запуски")}</div>}
        <Button size="sm" onClick={state.refresh}>Обновить запуски</Button>
        {state.runs.length === 0 && <div className="px-3 py-6 text-xs text-dim text-center">Нет запусков по фильтру</div>}
        {state.runs.map((run) => (
          <AdhocListRow key={run.id} run={run} active={run.id === state.selectedRun?.id} onSelect={() => state.setSelectedId(run.id)} />
        ))}
      </div>

      <div className="border-t border-token p-3 shrink-0">
        <div className="flex items-center gap-2 mb-2 text-xs">
          <Button size="sm" disabled={state.fetching || state.offset === 0} onClick={() => state.setOffset(Math.max(0, state.offset - 50))}>Назад</Button>
          <span>{state.total === 0 ? 0 : state.offset + 1}–{Math.min(state.offset + 50, state.total)} из {state.total}</span>
          <Button size="sm" disabled={state.fetching || state.offset + 50 >= state.total} onClick={() => state.setOffset(state.offset + 50)}>Далее</Button>
        </div>
        <Button
          variant="primary"
          size="sm"
          type="button"
          className="w-full flex items-center justify-center gap-2"
          onClick={() => setLaunchOpen(true)}
        >
          <Play className="w-3.5 h-3.5" />
          Запустить разовый тест
        </Button>
        <Button
          size="sm"
          type="button"
          className="w-full mt-2 flex items-center justify-center gap-2"
          disabled={recalcPending !== null}
          onClick={() => handleRecalc()}
          title="Пересчитать всю статистику в фоне, не блокируя очередь"
        >
          <BarChart3 className="w-3.5 h-3.5" />
          {recalcPending === "" ? "Запускаем…" : "Пересчитать статистику"}
        </Button>
        {categories.length > 0 && (
          <div className="mt-2">
            <div className="text-xs text-dim mb-1">Пересчитать отдельно</div>
            <div className="grid grid-cols-2 gap-1">
              {categories.map((category) => (
                <Button
                  key={category.key}
                  size="sm"
                  type="button"
                  className="w-full justify-center truncate"
                  disabled={recalcPending !== null}
                  onClick={() => handleRecalc(category.key, category.label)}
                  title={`Пересчитать статистику: ${category.label}`}
                >
                  {recalcPending === category.key ? "Запускаем…" : category.label}
                </Button>
              ))}
            </div>
          </div>
        )}
        {recalcRunning && (
          <div className="text-xs text-dim mt-1 flex items-center gap-1.5">
            <BarChart3 className="w-3.5 h-3.5 animate-pulse" />
            Идёт расчёт статистики{recalcCategoryLabel ? `: ${recalcCategoryLabel}` : ""}…
          </div>
        )}
      </div>
      {launchOpen && <StandaloneLaunchModal onClose={() => setLaunchOpen(false)} onLaunched={(id) => { state.setOffset(0); state.setSearch(""); state.setStatusFilter("all"); state.setSelectedId(id); state.refresh(); setLaunchOpen(false); }} />}
    </aside>
  );
}

function AdhocListRow({ run, active, onSelect }: { run: AdhocRun; active: boolean; onSelect: () => void }) {
  const meta = ADHOC_STATUS_META[run.status];
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left px-3 py-2 hover-bg flex flex-col gap-1 border-l-2 ${
        active ? "surface-2 border-accent" : "border-transparent"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="font-semibold mono text-sm truncate">{run.id}</span>
        {run.debugMode && <Badge kind="warn">Debug</Badge>}
        <Badge kind={meta.badge} className="shrink-0 ml-auto">{meta.label}</Badge>
      </div>
      <div className="text-xs truncate" title={run.testName}>{run.testCode}</div>
      <div className="mono text-[11px] text-dim truncate">{run.standName} · {run.mode} · {formatMsk(run.startedAt)}</div>
    </button>
  );
}

// ── рабочая зона: заголовок запуска + живой/завершённый лог ────────────────

export function AdhocWorkzone({ state }: { state: AdhocState }) {
  const [logOpen, setLogOpen] = useState(false);
  useEffect(() => setLogOpen(false), [state.selectedRun?.id]);
  const toast = useToast();
  const [retrying, setRetrying] = useState(false);
  const retryKeys = useRef<Record<string, string>>({});
  async function retry(id: string) {
    if (retrying) return;
    setRetrying(true);
    try {
      const item = await retryQueueItem(id, retryKeys.current[id] ??= crypto.randomUUID());
      state.setOffset(0); state.setSearch(""); state.setStatusFilter("all"); state.setSelectedId(item.id); state.refresh();
    } catch (error) { toast.error(apiErrMsg(error, "Не удалось повторить тест")); }
    finally { setRetrying(false); }
  }
  const run = state.selectedRun;
  const now = useNow();

  if (!run) {
    return <div className="surface border border-token rounded p-8 text-center text-dim">Нет выбранного запуска</div>;
  }

  if (logOpen) return <AttemptLogWorkzone id={run.id} state={run.status} logStatus={run.logStatus}
    title={`Лог · ${run.testCode}`} subtitle={`${run.standName} · ${run.rc} · ${run.kernel}`}
    canRetry={run.canRetry} onClose={() => setLogOpen(false)} onRetried={state.refresh} />;
  const meta = ADHOC_STATUS_META[run.status];
  const startedMs = new Date(run.startedAt).getTime();

  return (
    <div className="flex-1 min-h-0 overflow-auto grid gap-4 content-start">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg font-semibold mono">{run.id}</span>
            <Badge kind={meta.badge}>{meta.label}</Badge>
            {run.debugMode && <Badge kind="warn">Debug</Badge>}
            {run.status === "running" && startedMs !== null && (
              <span className="mono text-xs text-dim">прошло {formatElapsedHMS(now - startedMs)}</span>
            )}
          </div>
          <div className="text-xs text-dim mt-1">
            {run.testCode} · {run.testName} · {run.standName} · режим {run.mode} · запущен {formatMsk(run.startedAt)}
          </div>
        </div>
      </div>

      <div className="alert-warn text-xs">
        <Bug className="w-3.5 h-3.5 shrink-0" />
        <span>
          {run.debugMode ? "Debug: результат не засчитывается в СТП и прогон." : "Одиночный запуск: результат относится к выбранной СТП, вне счётчиков прогона."} РЦ {run.rc} · ядро {run.kernel}
        </span>
      </div>

      <div className="surface border border-token rounded p-4 grid gap-2">
        <h2 className="font-semibold">Результат одиночного теста</h2>
        <div>Результат: <Badge kind={meta.badge}>{meta.label}</Badge></div>
        <div className="text-sm">Начат: {formatMsk(run.startedAt)}</div>
        <div className="text-sm">Завершён: {formatMsk(run.finishedAt)}</div>
        <div className="text-sm">Длительность: {run.finishedAt ? formatElapsedHMS(new Date(run.finishedAt).getTime() - startedMs) : run.status === "running" ? formatElapsedHMS(now - startedMs) : "—"}</div>
      </div>
      {run.error && <div role="alert" className="text-xs text-danger">{run.error}</div>}
      <div className="flex items-center gap-2">
      {run.canRetry && <Button disabled={retrying} onClick={() => retry(run.id)}>Повторить тест</Button>}
      <Button disabled={run.logStatus === "rotated" || run.logStatus === "missing"} onClick={() => setLogOpen(true)}>Лог</Button>
      </div>
      <a className="text-accent text-sm" href={`/testing/logs?kind=standalone&attempt_id=${encodeURIComponent(run.id)}`}>Открыть в истории логов</a>
      {run.logStatus === "rotated" && <p className="text-xs text-dim">Лог ротирован. Результат сохранён.</p>}
    </div>
  );
}
