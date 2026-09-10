/**
 * Раздел «Прогоны» — fleet-wide кампании тестирования.
 *
 * Важное смысловое отличие от «запустить тест на одном стенде»: прогон
 * (`POST /test-runs`) — это запуск одного теста, закреплённого за каждым
 * стендом пула (`pinned_stand_id`), сразу на весь явно выбранный пул для
 * одного РЦ/ядра/режима. `final` отличает релиз-блокирующий прогон от
 * обычного/промежуточного. Прогон здесь — агрегированная кампания, а не
 * одна строчка "тест на стенде"; детали кампании — `TestRunDetail.queue_items`.
 *
 * Средняя панель Shell — список кампаний с поиском/фильтром/сортировкой,
 * рабочая зона — сводная статистика + детальная таблица выбранного прогона.
 * Состояние выбора живёт в `useRunsState`, вызываемом один раз в `Testing.tsx`
 * и общем для обеих половин (тот же паттерн, что и `useStpVersionState`).
 *
 * Источник истины backend: `testing_service/src/api/v1/endpoints/test_runs.py`.
 * `GET /test-runs` намеренно не отдаёт статистику по queue_items (только
 * `test_run_stands` — список id стендов пула) — разбивку passed/failed/…
 * видно только в детальной карточке (`getTestRun`), поэтому список кампаний
 * ниже не рисует прогресс-бар по исходам, как раньше на демо-данных.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  ListChecks,
  Play,
  Search,
  Server,
} from "lucide-react";
import { naturalCompare } from "@/lib/naturalSort";
import { formatMsk, formatMskShort, formatElapsedHMS } from "@/lib/datetime";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { createTestRun, getRunSummaryComment, getTestRun, listTestRuns } from "@/api/testing/testRuns";
import { getTestStand, listTestStands } from "@/api/testing/testStands";
import { getTestDefinition } from "@/api/testing/testDefinitions";
import { downloadTestLog, getTestLogText } from "@/api/testing/testLogs";
import type {
  RunSummaryCommentStatus,
  TestDefinition,
  TestRun,
  TestRunCreateRequest,
  TestRunQueueItem,
  TestRunStatus,
  TestStand,
} from "@/api/testing/types";
import { OS_VERSION_IDS as RC_IDS, OS_VERSIONS, SortableTh, Stat, useSortableRows, type BadgeKind } from "./_shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dropdown } from "@/components/ui/Dropdown";
import { Modal } from "@/components/ui/Modal";

const RUN_MODES = ["orel", "smolensk"] as const;
type RunMode = (typeof RUN_MODES)[number];
const MODE_LABELS: Record<RunMode, string> = { orel: "Орёл", smolensk: "Смоленск" };

const RUN_STATUS_META: Record<TestRunStatus, { label: string; badge: BadgeKind }> = {
  queued: { label: "В очереди", badge: "warn" },
  running: { label: "Выполняется", badge: "accent" },
  succeeded: { label: "Завершён", badge: "ok" },
  failed: { label: "Провален", badge: "danger" },
  partially_failed: { label: "Частично провален", badge: "warn" },
};

function runStatusMeta(status: string): { label: string; badge: BadgeKind } {
  return RUN_STATUS_META[status as TestRunStatus] ?? { label: status, badge: "warn" };
}

const QUEUE_ITEM_STATE_META: Record<string, { label: string; badge: BadgeKind }> = {
  queued: { label: "В очереди", badge: "warn" },
  preparing: { label: "Готовится стенд", badge: "warn" },
  ready: { label: "Готов к старту", badge: "info" },
  running: { label: "Выполняется", badge: "accent" },
  succeeded: { label: "Выполнено", badge: "ok" },
  failed: { label: "Провалено", badge: "danger" },
};

function queueItemStateMeta(state: string): { label: string; badge: BadgeKind } {
  return QUEUE_ITEM_STATE_META[state] ?? { label: state, badge: "warn" };
}

const SUMMARY_COMMENT_META: Record<RunSummaryCommentStatus, { label: string; badge: BadgeKind }> = {
  posted: { label: "отправлен в Confluence", badge: "ok" },
  skipped_no_blog: { label: "пропущен — не найден блог-пост", badge: "warn" },
  skipped_no_stp_page: { label: "пропущен — не найдена страница СТП", badge: "warn" },
  failed: { label: "ошибка отправки", badge: "danger" },
};

/** Тикающий `Date.now()` раз в секунду — единственный источник для realtime-элапсед-таймеров ниже, без опроса бэкенда. */
function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** По множеству id — карта `id → результат fetchOne(id)`, неудачные запросы просто выпадают из карты. */
function useByIds<T>(ids: string[], fetchOne: (id: string) => Promise<T>): Record<string, T> {
  const key = useMemo(() => Array.from(new Set(ids)).sort().join(","), [ids]);
  const q = useQuery(async () => {
    if (!key) return {} as Record<string, T>;
    const unique = key.split(",");
    const settled = await Promise.allSettled(unique.map((id) => fetchOne(id)));
    const map: Record<string, T> = {};
    settled.forEach((r, i) => {
      if (r.status === "fulfilled") map[unique[i]] = r.value;
    });
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return q.data ?? {};
}

function standLabel(stand: TestStand | undefined, id: string): string {
  const server = (stand?.server ?? null) as Record<string, unknown> | null;
  const name = (server?.["display_name"] ?? server?.["hostname"] ?? server?.["name"]) as string | undefined;
  return name || `${id.slice(0, 8)}…`;
}

function testLabel(def: TestDefinition | undefined, id: string): string {
  return def?.full_name || def?.code || `${id.slice(0, 8)}…`;
}

// ── состояние средней панели, общее для RunsMiddlePanel и RunsWorkzone ─────

export interface RunsState {
  runs: TestRun[];
  total: number;
  loading: boolean;
  error: unknown;
  refetch: () => void;
  search: string;
  setSearch: (v: string) => void;
  statusFilter: TestRunStatus | "all";
  setStatusFilter: (v: TestRunStatus | "all") => void;
  sortDir: "asc" | "desc";
  toggleSort: () => void;
  selectedId: string;
  setSelectedId: (id: string) => void;
  selectedRun: TestRun | null;
}

export function useRunsState(): RunsState {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<TestRunStatus | "all">("all");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedId, setSelectedId] = useState<string>("");

  const listQ = useQuery(
    () => listTestRuns({ limit: 200, ...(statusFilter === "all" ? {} : { status: statusFilter }) }),
    [statusFilter],
  );
  const allRuns = useMemo(() => listQ.data?.items ?? [], [listQ.data]);

  const runs = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = allRuns.filter((run) => {
      if (!term) return true;
      return run.id.toLowerCase().includes(term) || run.os_version_id.toLowerCase().includes(term);
    });
    return [...filtered].sort((a, b) =>
      sortDir === "asc" ? naturalCompare(a.id, b.id) : naturalCompare(b.id, a.id),
    );
  }, [allRuns, search, sortDir]);

  // Держим выбор синхронным со свежим списком: если текущий id пропал
  // (фильтр/поиск/удаление) — переключаемся на первую строку, а не показываем
  // "призрак" прошлого выбора.
  useEffect(() => {
    if (runs.length === 0) {
      if (selectedId) setSelectedId("");
      return;
    }
    if (!runs.some((r) => r.id === selectedId)) setSelectedId(runs[0].id);
  }, [runs, selectedId]);

  const selectedRun = allRuns.find((r) => r.id === selectedId) ?? null;

  return {
    runs,
    total: listQ.data?.total ?? allRuns.length,
    loading: listQ.loading,
    error: listQ.error,
    refetch: listQ.refetch,
    search,
    setSearch,
    statusFilter,
    setStatusFilter,
    sortDir,
    toggleSort: () => setSortDir((d) => (d === "asc" ? "desc" : "asc")),
    selectedId,
    setSelectedId,
    selectedRun,
  };
}

// ── средняя панель Shell: список кампаний ───────────────────────────────────

const STATUS_FILTER_OPTIONS: (TestRunStatus | "all")[] = [
  "all",
  "queued",
  "running",
  "succeeded",
  "failed",
  "partially_failed",
];

export function RunsMiddlePanel({ state }: { state: RunsState }) {
  const [launchOpen, setLaunchOpen] = useState(false);
  return (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${state.total} прогонам…`}
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
              {s === "all" ? "Все" : runStatusMeta(s).label}
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
        {state.loading && <div className="px-3 py-6 text-xs text-dim text-center">Загружаем прогоны…</div>}
        {!state.loading && !!state.error && (
          <div className="px-3 py-6 text-xs text-center">
            <div className="text-danger mb-2">{apiErrMsg(state.error, "Прогоны не загрузились")}</div>
            <Button size="sm" type="button" onClick={state.refetch}>Повторить</Button>
          </div>
        )}
        {!state.loading && !state.error && state.runs.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">Нет прогонов по фильтру</div>
        )}
        {state.runs.map((run) => (
          <RunListRow key={run.id} run={run} active={run.id === state.selectedId} onSelect={() => state.setSelectedId(run.id)} />
        ))}
      </div>

      <div className="border-t border-token p-3 shrink-0">
        <Button
          variant="primary"
          size="sm"
          type="button"
          className="w-full flex items-center justify-center gap-2"
          onClick={() => setLaunchOpen(true)}
        >
          <Play className="w-3.5 h-3.5" />
          Запустить прогон
        </Button>
      </div>

      {launchOpen && <LaunchRunModal state={state} onClose={() => setLaunchOpen(false)} />}
    </aside>
  );
}

function RunListRow({ run, active, onSelect }: { run: TestRun; active: boolean; onSelect: () => void }) {
  const meta = runStatusMeta(run.status);
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
        {run.final && <Badge kind="warn" className="shrink-0">финальный</Badge>}
        <Badge kind={meta.badge} className="shrink-0 ml-auto">{meta.label}</Badge>
      </div>
      <div className="mono text-[11px] text-dim truncate">
        {run.os_version_id} · {MODE_LABELS[run.mode as RunMode] ?? run.mode} · {run.kernel}
      </div>
      <div className="text-[11px] text-dim flex items-center justify-between gap-2">
        <span>стендов в пуле: {run.test_run_stands.length}</span>
        <span>{formatMskShort(run.created_at)}</span>
      </div>
    </button>
  );
}

// ── детальная таблица прогона: строка = один queue_item кампании ───────────

export function RunsWorkzone({ state }: { state: RunsState }) {
  const totals = useMemo(
    () => ({
      active: state.runs.filter((r) => r.status === "running").length,
      final: state.runs.filter((r) => r.final).length,
      succeeded: state.runs.filter((r) => r.status === "succeeded").length,
      avgStands: state.runs.length
        ? Math.round(state.runs.reduce((sum, r) => sum + r.test_run_stands.length, 0) / state.runs.length)
        : 0,
    }),
    [state.runs],
  );

  return (
    <div className="grid gap-4">
      <div>
        <h2 className="text-lg font-semibold">Прогоны — fleet-wide кампании</h2>
        <div className="text-sm text-dim mt-1">Каждый прогон — один тест на стенд для всего пула, для одного РЦ/ядра/режима</div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat title="Активные кампании" value={String(totals.active)} icon={Activity} kind="ok" />
        <Stat title="Финальных прогонов" value={String(totals.final)} icon={ListChecks} />
        <Stat title="Среднее число стендов" value={String(totals.avgStands)} icon={Server} />
        <Stat title="Успешных кампаний" value={String(totals.succeeded)} icon={CheckCircle2} kind="ok" />
      </div>

      {state.selectedRun ? (
        <RunDetailPanel run={state.selectedRun} />
      ) : (
        <div className="surface border border-token rounded p-8 text-center text-dim">
          {state.loading ? "Загружаем…" : "Нет выбранного прогона"}
        </div>
      )}
    </div>
  );
}

type RunQueueColumn = "testLabel" | "standLabel" | "state" | "startedAt";

interface RunQueueRow extends TestRunQueueItem {
  testLabel: string;
  standLabel: string;
}

function runQueueValue(row: RunQueueRow, column: RunQueueColumn): string | number {
  if (column === "startedAt") return row.started_at ? new Date(row.started_at).getTime() : -1;
  return row[column];
}

/**
 * Детальная разбивка выбранного прогона на дочерние `queue_items` —
 * сортируемая таблица с переходом в реальный лог прямо в строке. Имена
 * стендов/тестов дотягиваются по id отдельными `GET`-запросами, ограниченными
 * количеством различных стендов/тестов ОДНОГО прогона (не всего каталога) —
 * `GET /test-runs`/`GET /test-stands` намеренно не отдают такое обогащение
 * списком, чтобы не бить по `server_service` на каждую строку списка кампаний.
 */
function RunDetailPanel({ run }: { run: TestRun }) {
  const detailQ = useQuery(() => getTestRun(run.id), [run.id]);
  const summaryQ = useQuery(() => getRunSummaryComment(run.id), [run.id]);
  const items = useMemo(() => detailQ.data?.queue_items ?? [], [detailQ.data]);
  const now = useNow();

  const standIds = useMemo(() => items.map((i) => i.stand_id), [items]);
  const testIds = useMemo(() => items.map((i) => i.test_id), [items]);
  const standsById = useByIds(standIds, getTestStand);
  const testsById = useByIds(testIds, getTestDefinition);

  const rows = useMemo<RunQueueRow[]>(
    () =>
      items.map((item) => ({
        ...item,
        standLabel: standLabel(standsById[item.stand_id], item.stand_id),
        testLabel: testLabel(testsById[item.test_id], item.test_id),
      })),
    [items, standsById, testsById],
  );

  const { sorted, sort, onSort } = useSortableRows<RunQueueRow, RunQueueColumn>(rows, runQueueValue, {
    column: "standLabel",
    dir: "asc",
  });
  const [logTarget, setLogTarget] = useState<RunQueueRow | null>(null);

  const summary = summaryQ.data;

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-sm font-medium">Тесты прогона {run.id}</div>
          <div className="text-xs text-dim">
            {run.os_version_id} · {sorted.length} элементов очереди · создан {formatMsk(run.created_at)}
          </div>
        </div>
        <div className="text-xs text-dim flex items-center gap-2">
          <span>Confluence-комментарий:</span>
          {summaryQ.loading && <span>загрузка…</span>}
          {!summaryQ.loading && summary && summary.status && (
            <Badge kind={SUMMARY_COMMENT_META[summary.status as RunSummaryCommentStatus]?.badge ?? "warn"}>
              {SUMMARY_COMMENT_META[summary.status as RunSummaryCommentStatus]?.label ?? summary.status}
            </Badge>
          )}
          {!summaryQ.loading && summary && !summary.status && <span>ещё не отправлялся</span>}
        </div>
      </div>

      {detailQ.loading && <div className="p-4 text-xs text-dim text-center">Загружаем элементы очереди…</div>}
      {!detailQ.loading && !!detailQ.error && (
        <div className="p-4 text-xs text-center">
          <div className="text-danger mb-2">{apiErrMsg(detailQ.error, "Детали прогона не загрузились")}</div>
          <Button size="sm" type="button" onClick={detailQ.refetch}>Повторить</Button>
        </div>
      )}

      {!detailQ.loading && !detailQ.error && (
        <div className="overflow-auto max-h-[520px]">
          <table className="mini">
            <thead>
              <tr>
                <SortableTh label="Тест" column="testLabel" sort={sort} onSort={onSort} />
                <SortableTh label="Стенд" column="standLabel" sort={sort} onSort={onSort} />
                <SortableTh label="Статус" column="state" sort={sort} onSort={onSort} />
                <th>Retry</th>
                <SortableTh label="Начат" column="startedAt" sort={sort} onSort={onSort} />
                <th>Время</th>
                <th>Лог</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const meta = queueItemStateMeta(row.state);
                const startedMs = row.started_at ? new Date(row.started_at).getTime() : null;
                const finishedMs = row.finished_at ? new Date(row.finished_at).getTime() : null;
                const elapsed =
                  row.state === "running" && startedMs !== null
                    ? formatElapsedHMS(now - startedMs)
                    : startedMs !== null && finishedMs !== null
                      ? formatElapsedHMS(finishedMs - startedMs)
                      : "—";
                return (
                  <tr key={row.queue_item_id}>
                    <td>{row.testLabel}</td>
                    <td className="mono text-xs" title={row.stand_id}>{row.standLabel}</td>
                    <td>
                      <Badge kind={meta.badge}>{meta.label}</Badge>
                      {row.error && <div className="text-[10px] text-danger mt-0.5 max-w-[220px] truncate" title={row.error}>{row.error}</div>}
                    </td>
                    <td className="text-xs">{row.is_retry ? "повтор" : "—"}</td>
                    <td className="mono text-xs">{formatMskShort(row.started_at)}</td>
                    <td className="mono text-xs">{elapsed}</td>
                    <td>
                      <Button
                        size="sm"
                        type="button"
                        className="inline-flex items-center gap-1"
                        disabled={row.state === "queued"}
                        onClick={() => setLogTarget(row)}
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                        Лог
                      </Button>
                    </td>
                  </tr>
                );
              })}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center text-dim text-xs py-6">Очередь этого прогона пуста</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {logTarget && (
        <RunQueueItemLogModal
          queueItemId={logTarget.queue_item_id}
          title={logTarget.testLabel}
          subtitle={`${logTarget.standLabel} · ${queueItemStateMeta(logTarget.state).label}`}
          onClose={() => setLogTarget(null)}
        />
      )}
    </div>
  );
}

/** Просмотр полного текста реального лога `queue_item` + скачивание — без навигации по чекпоинтам (та есть в «Отладке»). */
function RunQueueItemLogModal({
  queueItemId,
  title,
  subtitle,
  onClose,
}: {
  queueItemId: string;
  title: string;
  subtitle: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const logQ = useQuery(() => getTestLogText(queueItemId), [queueItemId]);
  const [downloading, setDownloading] = useState(false);

  async function handleDownload() {
    setDownloading(true);
    try {
      await downloadTestLog(queueItemId);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось скачать лог"));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`Журнал · ${title}`}
      subtitle={subtitle}
      width="md"
      footer={
        <>
          <Button type="button" onClick={onClose}>Закрыть</Button>
          <Button variant="primary" type="button" onClick={handleDownload} disabled={downloading || logQ.loading || !!logQ.error}>
            {downloading ? "Скачиваем…" : "Скачать"}
          </Button>
        </>
      }
    >
      {logQ.loading && <div className="text-xs text-dim p-2">Загружаем лог…</div>}
      {!!logQ.error && <div className="alert-danger text-xs p-2">{apiErrMsg(logQ.error, "Лог не загрузился")}</div>}
      {logQ.data && <pre className="log-tail max-h-[60vh]">{logQ.data.text || "Лог пока пуст"}</pre>}
    </Modal>
  );
}

export function LaunchRunModal({ state, onClose }: { state: RunsState; onClose: () => void }) {
  const toast = useToast();
  const [rc, setRc] = useState(RC_IDS[0] ?? "");
  const [mode, setMode] = useState<RunMode>("orel");
  const kernelChoices = useMemo(() => OS_VERSIONS.find((v) => v.id === rc)?.kernels ?? [], [rc]);
  const [kernel, setKernel] = useState(kernelChoices[0] ?? "");
  const [final, setFinal] = useState(false);
  const [allStands, setAllStands] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  const standsQ = useQuery(() => listTestStands({ is_active: true, queue_enabled: true, limit: 500 }), []);
  const stands = useMemo(() => standsQ.data?.items ?? [], [standsQ.data]);

  // При смене РЦ ядро, если оно больше не входит в новый список, сбрасывается на первое доступное.
  useEffect(() => {
    if (!kernelChoices.includes(kernel)) setKernel(kernelChoices[0] ?? "");
  }, [kernelChoices, kernel]);

  const poolIds = allStands ? stands.map((s) => s.id) : Array.from(selected);

  async function handleSubmit() {
    if (!rc || !kernel || poolIds.length === 0) return;
    const body: TestRunCreateRequest = { os_version_id: rc, mode, kernel, test_run_stands: poolIds, final };
    setSubmitting(true);
    try {
      const res = await createTestRun(body);
      if (res.stands_without_tests.length > 0 || res.enqueue_errors.length > 0) {
        toast.info(
          `Прогон ${res.id} создан частично: без закреплённого теста — ${res.stands_without_tests.length}, ошибок постановки — ${res.enqueue_errors.length}`,
        );
      } else {
        toast.success(`Прогон ${res.id} запущен на ${poolIds.length} стендах`);
      }
      state.refetch();
      state.setSelectedId(res.id);
      onClose();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось запустить прогон"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Запустить прогон"
      subtitle="Один тест на стенд для всего пула, для одного РЦ/ядра/режима"
      width="md"
      footer={
        <>
          <Button type="button" onClick={onClose}>Отмена</Button>
          <Button
            variant="primary"
            type="button"
            onClick={handleSubmit}
            disabled={submitting || !rc || !kernel || poolIds.length === 0}
          >
            {submitting ? "Запускаем…" : `Запустить прогон${final ? " (финальный)" : ""}`}
          </Button>
        </>
      }
    >
        <div className="grid gap-4">
          <label className="grid gap-1">
            <span className="text-xs text-dim">РЦ</span>
            <Dropdown
              mode="single"
              searchable
              options={RC_IDS.map((id) => ({ value: id, label: id }))}
              value={rc}
              onChange={setRc}
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1">
              <span className="text-xs text-dim">Режим безопасности</span>
              <Dropdown
                mode="single"
                options={RUN_MODES.map((m) => ({ value: m, label: MODE_LABELS[m] }))}
                value={mode}
                onChange={(v) => setMode(v as RunMode)}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-xs text-dim">Ядро</span>
              <Dropdown
                mode="single"
                options={kernelChoices.map((k) => ({ value: k, label: k }))}
                value={kernel}
                onChange={setKernel}
                placeholder={kernelChoices.length ? "Все" : "нет ядер для этого РЦ"}
                disabled={kernelChoices.length === 0}
              />
            </label>
          </div>

          <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
            <Checkbox checked={final} onChange={(e) => setFinal(e.target.checked)} className="mt-0.5" />
            <span>
              <span className="text-sm font-medium block">Финальный прогон</span>
              <span className="text-xs text-dim">Блокирует релиз РЦ до получения результата; отображается отдельным флагом в кампаниях</span>
            </span>
          </label>

          <div className="surface-2 border border-token rounded p-3">
            {standsQ.loading && <div className="text-xs text-dim">Загружаем пул стендов…</div>}
            {!!standsQ.error && (
              <div className="text-xs text-danger">{apiErrMsg(standsQ.error, "Стенды не загрузились")}</div>
            )}
            {!standsQ.loading && !standsQ.error && (
              <>
                <label className="flex items-center gap-2 cursor-pointer mb-2">
                  <Checkbox checked={allStands} onChange={(e) => setAllStands(e.target.checked)} />
                  <span className="text-sm font-medium">Все активные стенды пула ({stands.length})</span>
                </label>
                {!allStands && (
                  <Dropdown
                    mode="multi"
                    searchable
                    placeholder="Выберите стенды"
                    options={stands.map((s) => ({ value: s.id, label: standLabel(s, s.id) }))}
                    value={selected}
                    onChange={setSelected}
                  />
                )}
                {!allStands && <div className="text-xs text-dim mt-2">Выбрано стендов: {selected.size}</div>}
              </>
            )}
          </div>
        </div>
    </Modal>
  );
}
