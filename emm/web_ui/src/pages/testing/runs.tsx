/**
 * Раздел «Прогоны» — fleet-wide кампании тестирования.
 *
 * Важное смысловое отличие от «запустить тест на одном стенде»: прогон
 * (`TestrunManager.create_test_run(version, final)` в allta_app) — это
 * запуск ВСЕГО набора тестов на ВСЕХ стендах пула сразу для одного РЦ.
 * `final` отличает релиз-блокирующий прогон от обычного/промежуточного.
 * Поэтому здесь прогон — это агрегированный прогресс по пулу, а не строчка
 * "тест на стенде".
 *
 * Средняя панель Shell — список кампаний с поиском/фильтром/сортировкой,
 * рабочая зона — сводная статистика + детальная таблица выбранного прогона.
 * Тот же паттерн, что и в `stp.tsx` (`useStpVersionState` / `StpMiddlePanel`
 * / `StpWorkzone`): состояние выбора живёт в одном хуке, вызываемом один раз
 * в `Testing.tsx` и общем для обеих половин.
 */
import { useMemo, useState } from "react";
import { Activity, CheckCircle2, ChevronDown, ChevronUp, ExternalLink, ListChecks, Play, Search, Server } from "lucide-react";
import { naturalCompare } from "@/lib/naturalSort";
import { TEST_CATALOG } from "./tests";
import { Dropdown } from "@/components/ui/Dropdown";
import {
  LogViewerModal,
  OS_VERSION_IDS as RC_IDS,
  QUEUE_TEXT,
  queueBadge,
  SortableTh,
  Stat,
  STANDS,
  useSortableRows,
  type QueueItem,
  type QueueState,
  type Stand,
} from "./_shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";

export type RunStatus = "running" | "completed" | "failed";

export interface TestRunTotals {
  passed: number;
  failed: number;
  running: number;
  pending: number;
}

export interface TestRun {
  id: string;
  rcId: string;
  final: boolean;
  startedAt: string;
  status: RunStatus;
  standsTotal: number;
  standsDone: number;
  totals: TestRunTotals;
}

export const RUNS: TestRun[] = [
  {
    id: "run-2026090301",
    rcId: RC_IDS[0],
    final: true,
    startedAt: "03.09.2026 09:12 MSK",
    status: "running",
    standsTotal: 18,
    standsDone: 6,
    totals: { passed: 214, failed: 9, running: 18, pending: 96 },
  },
  {
    id: "run-2026090202",
    rcId: RC_IDS[1],
    final: false,
    startedAt: "02.09.2026 14:40 MSK",
    status: "running",
    standsTotal: 12,
    standsDone: 3,
    totals: { passed: 88, failed: 4, running: 12, pending: 140 },
  },
  {
    id: "run-2026090108",
    rcId: RC_IDS[2],
    final: false,
    startedAt: "01.09.2026 20:05 MSK",
    status: "completed",
    standsTotal: 3,
    standsDone: 3,
    totals: { passed: 41, failed: 1, running: 0, pending: 0 },
  },
  {
    id: "run-2026083005",
    rcId: RC_IDS[0],
    final: false,
    startedAt: "30.08.2026 11:00 MSK",
    status: "completed",
    standsTotal: 20,
    standsDone: 20,
    totals: { passed: 612, failed: 18, running: 0, pending: 0 },
  },
  {
    id: "run-2026082901",
    rcId: RC_IDS[4],
    final: false,
    startedAt: "29.08.2026 08:30 MSK",
    status: "failed",
    standsTotal: 20,
    standsDone: 20,
    totals: { passed: 120, failed: 302, running: 0, pending: 0 },
  },
  {
    id: "run-2026072210",
    rcId: RC_IDS[5],
    final: true,
    startedAt: "22.07.2026 07:00 MSK",
    status: "completed",
    standsTotal: 20,
    standsDone: 20,
    totals: { passed: 598, failed: 5, running: 0, pending: 0 },
  },
];

const RUN_STATUS_META: Record<RunStatus, { label: string; badge: "ok" | "danger" | "accent" }> = {
  running: { label: "Выполняется", badge: "accent" },
  completed: { label: "Завершён", badge: "ok" },
  failed: { label: "Провален", badge: "danger" },
};

// ── состояние средней панели, общее для RunsMiddlePanel и RunsWorkzone ─────

export interface RunsState {
  runs: TestRun[];
  total: number;
  search: string;
  setSearch: (v: string) => void;
  statusFilter: RunStatus | "all";
  setStatusFilter: (v: RunStatus | "all") => void;
  sortDir: "asc" | "desc";
  toggleSort: () => void;
  selectedId: string;
  setSelectedId: (id: string) => void;
  selectedRun: TestRun | null;
}

export function useRunsState(): RunsState {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<RunStatus | "all">("all");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedId, setSelectedId] = useState<string>(RUNS[0]?.id ?? "");

  const runs = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = RUNS.filter((run) => {
      if (statusFilter !== "all" && run.status !== statusFilter) return false;
      if (!term) return true;
      return run.id.toLowerCase().includes(term) || run.rcId.toLowerCase().includes(term);
    });
    // id несёт дату прогона (run-YYYYMMDDNN), поэтому натуральная сортировка
    // по id совпадает с хронологической — отдельный парсер даты не нужен.
    return [...filtered].sort((a, b) =>
      sortDir === "asc" ? naturalCompare(a.id, b.id) : naturalCompare(b.id, a.id),
    );
  }, [search, statusFilter, sortDir]);

  const selectedRun = RUNS.find((r) => r.id === selectedId) ?? RUNS[0] ?? null;

  return {
    runs,
    total: RUNS.length,
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

const STATUS_FILTER_OPTIONS: (RunStatus | "all")[] = ["all", "running", "completed", "failed"];

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
              {s === "all" ? "Все" : RUN_STATUS_META[s].label}
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
        {state.runs.length === 0 && <div className="px-3 py-6 text-xs text-dim text-center">Нет прогонов по фильтру</div>}
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

      {launchOpen && <LaunchRunModal onClose={() => setLaunchOpen(false)} />}
    </aside>
  );
}

function RunListRow({ run, active, onSelect }: { run: TestRun; active: boolean; onSelect: () => void }) {
  const meta = RUN_STATUS_META[run.status];
  const total = run.totals.passed + run.totals.failed + run.totals.running + run.totals.pending || 1;
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
      <div className="mono text-[11px] text-dim truncate">{run.rcId} · {run.startedAt}</div>
      <div className="h-1 rounded-full overflow-hidden surface-2 border border-token flex">
        <span className="bg-[var(--ok)]" style={{ width: `${(run.totals.passed / total) * 100}%` }} />
        <span className="bg-[var(--danger)]" style={{ width: `${(run.totals.failed / total) * 100}%` }} />
        <span className="bg-[var(--accent)]" style={{ width: `${(run.totals.running / total) * 100}%` }} />
        <span className="bg-[var(--warn)]" style={{ width: `${(run.totals.pending / total) * 100}%` }} />
      </div>
      <div className="text-[11px] text-dim">стенды {run.standsDone}/{run.standsTotal}</div>
    </button>
  );
}

// ── детальная таблица прогона: строка = один тест на одном стенде ──────────

const RUN_MODES = ["orel", "smolensk"] as const;
type RunMode = (typeof RUN_MODES)[number];

interface RunTestRow {
  id: string;
  test: string;
  os: string;
  kernel: string;
  mode: RunMode;
  standName: string;
  standId: number;
  status: QueueState;
  minutes: number;
}

type RunTestColumn = "test" | "os" | "kernel" | "mode" | "standName" | "status" | "minutes";

/**
 * Демо-разбивка fleet-кампании на отдельные тесты — по образцу того, как
 * Zephyr Scale хранит результаты прогона: тест-кейс × комбинация
 * ядро/режим/стенд. Точное совпадение чисел с агрегированными totals прогона
 * не требуется, это витрина, не бухгалтерия.
 */
function buildRunTests(run: TestRun): RunTestRow[] {
  const testNames = TEST_CATALOG.slice(0, 6).map((t) => t.fullName);
  const stands = STANDS.filter((s) => s.status !== "offline").slice(0, Math.max(3, Math.min(run.standsTotal, 6)));
  const rows: RunTestRow[] = [];
  stands.forEach((stand, standIdx) => {
    const mode: RunMode = RUN_MODES[standIdx % RUN_MODES.length];
    testNames.forEach((test, testIdx) => {
      const seed = (standIdx * 7 + testIdx * 3) % 10;
      let status: QueueState;
      if (run.status === "completed") status = seed === 0 ? "failed" : "done";
      else if (run.status === "failed") status = seed < 6 ? "failed" : "done";
      else status = seed < 2 ? "running" : seed < 3 ? "failed" : seed < 7 ? "done" : "pending";
      rows.push({
        id: `${run.id}-${stand.id}-${testIdx}`,
        test,
        os: stand.os,
        kernel: stand.kernel,
        mode,
        standName: stand.name,
        standId: stand.id,
        status,
        minutes: status === "pending" ? -1 : 4 + seed * 3,
      });
    });
  });
  return rows;
}

function runTestValue(row: RunTestRow, column: RunTestColumn): string | number {
  if (column === "minutes") return row.minutes;
  return row[column];
}

export function RunsWorkzone({ state }: { state: RunsState }) {
  const totals = useMemo(
    () => ({
      active: RUNS.filter((r) => r.status === "running").length,
      final: RUNS.filter((r) => r.final).length,
      passRate: (() => {
        const totalPassed = RUNS.reduce((sum, r) => sum + r.totals.passed, 0);
        const totalRun = RUNS.reduce((sum, r) => sum + r.totals.passed + r.totals.failed, 0);
        return totalRun ? Math.round((totalPassed / totalRun) * 100) : 0;
      })(),
      avgStands: Math.round(RUNS.reduce((sum, r) => sum + r.standsTotal, 0) / RUNS.length),
    }),
    [],
  );

  return (
    <div className="grid gap-4">
      <div>
        <h2 className="text-lg font-semibold">Прогоны — fleet-wide кампании</h2>
        <div className="text-sm text-dim mt-1">Каждый прогон — весь набор тестов на всех стендах пула для одного РЦ</div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat title="Активные кампании" value={String(totals.active)} icon={Activity} kind="ok" />
        <Stat title="Финальных прогонов" value={String(totals.final)} icon={ListChecks} />
        <Stat title="Среднее число стендов" value={String(totals.avgStands)} icon={Server} />
        <Stat title="Успешность по пулу" value={`${totals.passRate}%`} icon={CheckCircle2} kind={totals.passRate >= 90 ? "ok" : "warn"} />
      </div>

      {state.selectedRun ? (
        <RunDetailPanel run={state.selectedRun} />
      ) : (
        <div className="surface border border-token rounded p-8 text-center text-dim">Нет выбранного прогона</div>
      )}
    </div>
  );
}

/**
 * Детальная разбивка выбранного прогона на отдельные тесты — сортируемая
 * таблица с переходом в лог прямо в строке, без ухода на другую страницу.
 */
function RunDetailPanel({ run }: { run: TestRun }) {
  const rows = useMemo(() => buildRunTests(run), [run]);
  const { sorted, sort, onSort } = useSortableRows<RunTestRow, RunTestColumn>(rows, runTestValue, {
    column: "standName",
    dir: "asc",
  });
  const [logTarget, setLogTarget] = useState<{ stand: Stand; item: QueueItem } | null>(null);

  const openLog = (row: RunTestRow) => {
    const stand = STANDS.find((s) => s.id === row.standId);
    if (!stand) return;
    setLogTarget({
      stand,
      item: {
        title: row.test,
        state: row.status,
        meta: `${row.mode} · ${row.kernel}`,
        log: row.status === "pending" ? undefined : `/logs/${stand.name}/${row.test.replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase()}.txt`,
      },
    });
  };

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-sm font-medium">Тесты прогона {run.id}</div>
          <div className="text-xs text-dim">{run.rcId} · {sorted.length} строк · сортировка по клику на заголовок</div>
        </div>
      </div>
      <div className="overflow-auto max-h-[520px]">
        <table className="mini">
          <thead>
            <tr>
              <SortableTh label="Тест" column="test" sort={sort} onSort={onSort} />
              <SortableTh label="ОС / релиз" column="os" sort={sort} onSort={onSort} />
              <SortableTh label="Ядро" column="kernel" sort={sort} onSort={onSort} />
              <SortableTh label="Режим" column="mode" sort={sort} onSort={onSort} />
              <SortableTh label="Стенд" column="standName" sort={sort} onSort={onSort} />
              <SortableTh label="Статус" column="status" sort={sort} onSort={onSort} />
              <SortableTh label="Время" column="minutes" sort={sort} onSort={onSort} />
              <th>Лог</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.id}>
                <td>{row.test}</td>
                <td className="text-xs text-dim">{row.os}</td>
                <td className="mono text-xs">{row.kernel}</td>
                <td className="mono text-xs">{row.mode}</td>
                <td className="mono text-xs">{row.standName}</td>
                <td>
                  <span className={`badge badge-${queueBadge(row.status)}`}>{QUEUE_TEXT[row.status]}</span>
                </td>
                <td className="mono text-xs">{row.minutes < 0 ? "—" : `${row.minutes} мин`}</td>
                <td>
                  <Button size="sm"
                    type="button"
                    className="inline-flex items-center gap-1"
                    disabled={row.status === "pending"}
                    onClick={() => openLog(row)}
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                    Лог
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {logTarget && (
        <LogViewerModal stand={logTarget.stand} item={logTarget.item} onClose={() => setLogTarget(null)} />
      )}
    </div>
  );
}

export function LaunchRunModal({ onClose }: { onClose: () => void }) {
  const [rc, setRc] = useState(RC_IDS[0]);
  const [final, setFinal] = useState(false);
  const [allStands, setAllStands] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set(STANDS.map((s) => s.id)));

  const toggleStand = (stand: Stand) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(stand.id)) next.delete(stand.id);
      else next.add(stand.id);
      return next;
    });
  };

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Запустить прогон"
      subtitle="Весь набор тестов на выбранных стендах пула для одного РЦ"
      width="md"
      footer={
        <>
          <Button type="button" onClick={onClose}>Отмена</Button>
          <Button variant="primary" type="button" onClick={onClose}>
            Запустить прогон{final ? " (финальный)" : ""}
          </Button>
        </>
      }
    >
        <div className="grid gap-4">
          <label className="grid gap-1">
            <span className="text-xs text-dim">РЦ</span>
            <Dropdown
              mode="single"
              options={RC_IDS.map((id) => ({ value: id, label: id }))}
              value={rc}
              onChange={setRc}
            />
          </label>

          <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
            <Checkbox checked={final} onChange={(e) => setFinal(e.target.checked)} className="mt-0.5" />
            <span>
              <span className="text-sm font-medium block">Финальный прогон</span>
              <span className="text-xs text-dim">Блокирует релиз РЦ до получения результата; отображается отдельным флагом в кампаниях</span>
            </span>
          </label>

          <div className="surface-2 border border-token rounded p-3">
            <label className="flex items-center gap-2 cursor-pointer mb-2">
              <Checkbox
                checked={allStands}
                onChange={(e) => {
                  setAllStands(e.target.checked);
                  if (e.target.checked) setSelected(new Set(STANDS.map((s) => s.id)));
                }}
              />
              <span className="text-sm font-medium">Все стенды пула ({STANDS.length})</span>
            </label>
            {!allStands && (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5 max-h-52 overflow-auto">
                {STANDS.map((stand) => (
                  <label key={stand.id} className="flex items-center gap-2 text-xs surface border border-token rounded px-2 py-1.5">
                    <Checkbox checked={selected.has(stand.id)} onChange={() => toggleStand(stand)} />
                    <span className="truncate">{stand.name}</span>
                  </label>
                ))}
              </div>
            )}
            {!allStands && <div className="text-xs text-dim mt-2">Выбрано стендов: {selected.size}</div>}
          </div>

        </div>
    </Modal>
  );
}
