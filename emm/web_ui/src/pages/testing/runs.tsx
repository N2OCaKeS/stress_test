/**
 * Раздел «Прогоны» — fleet-wide кампании тестирования.
 *
 * Важное смысловое отличие от «запустить тест на одном стенде»: прогон
 * (`TestrunManager.create_test_run(version, final)` в allta_app) — это
 * запуск ВСЕГО набора тестов на ВСЕХ стендах пула сразу для одного РЦ.
 * `final` отличает релиз-блокирующий прогон от обычного/промежуточного.
 * Поэтому здесь прогон — это агрегированный прогресс по пулу, а не строчка
 * "тест на стенде".
 */
import { useMemo, useState } from "react";
import { Activity, CheckCircle2, ExternalLink, ListChecks, Play, Server, TimerReset, XCircle } from "lucide-react";
import { TEST_CATALOG } from "./tests";
import {
  LogViewerModal,
  ModalHeader,
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

export function RunsWorkzone() {
  const [launchOpen, setLaunchOpen] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string>(RUNS[0]?.id ?? "");
  const selectedRun = RUNS.find((r) => r.id === selectedRunId) ?? RUNS[0] ?? null;

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
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold">Прогоны — fleet-wide кампании</h2>
          <div className="text-sm text-dim mt-1">Каждый прогон — весь набор тестов на всех стендах пула для одного РЦ</div>
        </div>
        <button type="button" className="btn btn-primary inline-flex items-center gap-2" onClick={() => setLaunchOpen(true)}>
          <Play className="w-4 h-4" />
          Запустить прогон
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Stat title="Активные кампании" value={String(totals.active)} icon={Activity} kind="ok" />
        <Stat title="Финальных прогонов" value={String(totals.final)} icon={ListChecks} />
        <Stat title="Среднее число стендов" value={String(totals.avgStands)} icon={Server} />
        <Stat title="Успешность по пулу" value={`${totals.passRate}%`} icon={CheckCircle2} kind={totals.passRate >= 90 ? "ok" : "warn"} />
      </div>

      <div>
        <div className="text-sm font-medium mb-2">Кампании ({RUNS.length})</div>
        <div className="grid gap-2">
          {RUNS.map((run) => (
            <RunRow key={run.id} run={run} selected={run.id === selectedRunId} onSelect={() => setSelectedRunId(run.id)} />
          ))}
        </div>
      </div>

      {selectedRun && <RunDetailPanel run={selectedRun} />}

      {launchOpen && <LaunchRunModal onClose={() => setLaunchOpen(false)} />}
    </div>
  );
}

function RunRow({ run, selected, onSelect }: { run: TestRun; selected: boolean; onSelect: () => void }) {
  const meta = RUN_STATUS_META[run.status];
  const total = run.totals.passed + run.totals.failed + run.totals.running + run.totals.pending || 1;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`surface border rounded p-4 grid grid-cols-1 lg:grid-cols-[200px_minmax(0,1fr)_170px] gap-3 items-center text-left hover-bg ${
        selected ? "border-accent" : "border-token"
      }`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-semibold mono truncate">{run.id}</span>
          {run.final && <span className="badge badge-warn">финальный</span>}
        </div>
        <div className="mono text-xs text-dim mt-1 truncate">{run.rcId}</div>
        <div className="text-xs text-dim mt-1">{run.startedAt}</div>
      </div>
      <div>
        <div className="text-xs text-dim mb-1 flex gap-3 flex-wrap">
          <span>стенды {run.standsDone}/{run.standsTotal}</span>
          <span className="text-ok">passed {run.totals.passed}</span>
          <span className="text-danger">failed {run.totals.failed}</span>
          <span className="text-accent">running {run.totals.running}</span>
          <span className="text-warn">pending {run.totals.pending}</span>
        </div>
        <div className="h-1.5 rounded-full overflow-hidden surface-2 border border-token flex">
          <span className="bg-[var(--ok)]" style={{ width: `${(run.totals.passed / total) * 100}%` }} />
          <span className="bg-[var(--danger)]" style={{ width: `${(run.totals.failed / total) * 100}%` }} />
          <span className="bg-[var(--accent)]" style={{ width: `${(run.totals.running / total) * 100}%` }} />
          <span className="bg-[var(--warn)]" style={{ width: `${(run.totals.pending / total) * 100}%` }} />
        </div>
      </div>
      <div className="flex justify-start lg:justify-end">
        <span className={`badge badge-${meta.badge} inline-flex items-center gap-1`}>
          {run.status === "failed" ? <XCircle className="w-3 h-3" /> : <TimerReset className="w-3 h-3" />}
          {meta.label}
        </span>
      </div>
    </button>
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
                  <button
                    type="button"
                    className="btn btn-sm inline-flex items-center gap-1"
                    disabled={row.status === "pending"}
                    onClick={() => openLog(row)}
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                    Лог
                  </button>
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
    <div className="fixed inset-0 z-50 bg-black/55 flex items-center justify-center p-5">
      <div className="surface border border-token rounded w-full max-w-2xl max-h-[86vh] overflow-hidden shadow-2xl">
        <ModalHeader
          title="Запустить прогон"
          subtitle="Весь набор тестов на выбранных стендах пула для одного РЦ"
          onClose={onClose}
        />
        <div className="p-4 grid gap-4 overflow-auto max-h-[calc(86vh-64px)]">
          <label className="grid gap-1">
            <span className="text-xs text-dim">РЦ</span>
            <select className="input" value={rc} onChange={(e) => setRc(e.target.value)}>
              {RC_IDS.map((id) => <option key={id}>{id}</option>)}
            </select>
          </label>

          <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
            <input type="checkbox" checked={final} onChange={(e) => setFinal(e.target.checked)} className="mt-0.5" />
            <span>
              <span className="text-sm font-medium block">Финальный прогон</span>
              <span className="text-xs text-dim">Блокирует релиз РЦ до получения результата; отображается отдельным флагом в кампаниях</span>
            </span>
          </label>

          <div className="surface-2 border border-token rounded p-3">
            <label className="flex items-center gap-2 cursor-pointer mb-2">
              <input
                type="checkbox"
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
                    <input type="checkbox" checked={selected.has(stand.id)} onChange={() => toggleStand(stand)} />
                    <span className="truncate">{stand.name}</span>
                  </label>
                ))}
              </div>
            )}
            {!allStands && <div className="text-xs text-dim mt-2">Выбрано стендов: {selected.size}</div>}
          </div>

          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={onClose}>Отмена</button>
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Запустить прогон{final ? " (финальный)" : ""}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
