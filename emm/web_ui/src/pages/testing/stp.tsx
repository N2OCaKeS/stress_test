/**
 * Раздел «СТП» — Состав Тестового Прогона, сводная таблица результатов из
 * внешней системы Jira Zephyr Scale для одного РЦ.
 *
 * Реальная форма — транспонированная матрица (`ZefirResultTable`): строки —
 * тест-кейсы, колонки — комбинации ядро×режим(orel/smolensk)×стенд. Для
 * демо-шаблона матрица разложена в плоскую сортируемую таблицу — каждая
 * строка уже и есть одна такая комбинация, без вложенной раскрывашки.
 *
 * emm здесь не источник истины: сегодня legacy публикует эту таблицу на
 * Confluence (`life.astralinux.ru`) вручную по кнопке, без крона. Будущая
 * архитектура emm сможет дёргать тот же Zephyr REST API напрямую, минуя
 * Confluence, но витрина остаётся зеркалом — редактировать статусы отсюда
 * нельзя.
 */
import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  Clock3,
  ExternalLink,
  ListChecks,
  Loader2,
  RefreshCcw,
  RefreshCw,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { RC_LIST, RC_IDS, type ReleaseCandidate } from "./rc";
import { STANDS } from "./_shared";
import {
  LogViewerModal,
  SortableTh,
  Stat,
  useSortableRows,
  type QueueItem,
  type Stand,
} from "./_shared";

export type StpStatus = "not_run" | "in_progress" | "done" | "failed";
export type StpMode = "orel" | "smolensk";

export interface StpRow {
  id: string;
  testCode: string;
  testTitle: string;
  kernel: string;
  mode: StpMode;
  standName: string;
  standId: number;
  status: StpStatus;
}

type StpColumn = "testCode" | "kernel" | "mode" | "standName" | "status";

const STATUS_META: Record<StpStatus, { label: string; icon: LucideIcon; badge?: "ok" | "danger" | "warn" }> = {
  not_run: { label: "Не запускался", icon: Clock3 },
  in_progress: { label: "Выполняется", icon: Loader2, badge: "warn" },
  done: { label: "Выполнено", icon: CheckCircle2, badge: "ok" },
  failed: { label: "Провалено", icon: XCircle, badge: "danger" },
};

const STP_TEST_CASES = [
  { code: "ASTRA-T101", title: "Установка с загрузочного носителя" },
  { code: "ASTRA-T102", title: "Настройка режима Смоленск (МРД+МКЦ)" },
  { code: "ASTRA-T103", title: "Присоединение к домену FreeIPA" },
  { code: "ASTRA-T104", title: "PostgreSQL: базовое резервное копирование" },
  { code: "ASTRA-T105", title: "Сетевой стек: iptables + nftables совместимость" },
  { code: "ASTRA-T106", title: "Аудит: пересылка событий в syslog" },
];

const STP_TEST_CASES_HOTFIX = [
  { code: "ASTRA-T301", title: "Хотфикс: регресс сетевого драйвера" },
  { code: "ASTRA-T302", title: "Хотфикс: проверка совместимости с предыдущим ядром" },
];

const STP_MODES: StpMode[] = ["orel", "smolensk"];

type StpPattern = "mostly_done" | "in_progress" | "hotfix";

/**
 * Демо-генератор строк матрицы для одного РЦ — тест-кейс × ядро × режим ×
 * стенд, с распределением статусов по паттерну, характерному для этапа
 * жизни РЦ (свежий хотфикс, кампания в процессе, почти завершённый прогон).
 */
function buildStpDataset(rc: ReleaseCandidate, pattern: StpPattern, testCases: { code: string; title: string }[]): StpRow[] {
  const stands = STANDS.filter((s) => s.status !== "offline").slice(0, 3);
  const kernels = rc.kernels.length ? rc.kernels : ["6.12.24-1.el11"];
  const rows: StpRow[] = [];
  let i = 0;
  for (const test of testCases) {
    for (const kernel of kernels) {
      for (const mode of STP_MODES) {
        for (const stand of stands) {
          i += 1;
          const seed = i % 10;
          let status: StpStatus;
          if (pattern === "mostly_done") status = seed === 0 ? "failed" : seed === 1 ? "in_progress" : "done";
          else if (pattern === "in_progress") status = seed < 3 ? "done" : seed < 5 ? "in_progress" : seed === 5 ? "failed" : "not_run";
          else status = seed < 8 ? "done" : "failed";
          rows.push({
            id: `${rc.id}-${test.code}-${kernel}-${mode}-${stand.id}`,
            testCode: test.code,
            testTitle: test.title,
            kernel,
            mode,
            standName: stand.name,
            standId: stand.id,
            status,
          });
        }
      }
    }
  }
  return rows;
}

const STP_DATASETS: Record<string, StpRow[]> = (() => {
  const map: Record<string, StpRow[]> = {};
  if (RC_LIST[0]) map[RC_LIST[0].id] = buildStpDataset(RC_LIST[0], "mostly_done", STP_TEST_CASES);
  if (RC_LIST[1]) map[RC_LIST[1].id] = buildStpDataset(RC_LIST[1], "in_progress", STP_TEST_CASES);
  if (RC_LIST[2]) map[RC_LIST[2].id] = buildStpDataset(RC_LIST[2], "hotfix", STP_TEST_CASES_HOTFIX);
  return map;
})();

function stpValue(row: StpRow, column: StpColumn): string | number {
  return row[column];
}

const CONFLUENCE_URL = "https://life.astralinux.ru/pages/viewpage.action?spaceKey=DEVQA&title=";
const ZEPHYR_CYCLE_URL = "https://jira.astralinux.ru/secure/Tests.jspa#/testPlayer";

export function StpWorkzone() {
  const [rcId, setRcId] = useState<string>(RC_IDS.find((id) => STP_DATASETS[id]) ?? RC_IDS[0]);
  const [updatedAt, setUpdatedAt] = useState<Record<string, number>>(() =>
    Object.fromEntries(Object.keys(STP_DATASETS).map((id) => [id, Date.now()])),
  );
  const [now, setNow] = useState(() => Date.now());
  const [logTarget, setLogTarget] = useState<{ stand: Stand; item: QueueItem } | null>(null);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const rows = useMemo(() => STP_DATASETS[rcId] ?? [], [rcId]);
  const { sorted, sort, onSort } = useSortableRows<StpRow, StpColumn>(rows, stpValue, { column: "testCode", dir: "asc" });

  const totals = useMemo(
    () => ({
      total: rows.length,
      done: rows.filter((r) => r.status === "done").length,
      failed: rows.filter((r) => r.status === "failed").length,
      inProgress: rows.filter((r) => r.status === "in_progress").length,
      notRun: rows.filter((r) => r.status === "not_run").length,
    }),
    [rows],
  );

  const refreshOne = () => setUpdatedAt((current) => ({ ...current, [rcId]: Date.now() }));
  const refreshAll = () => {
    const stamp = Date.now();
    setUpdatedAt((current) => {
      const next = { ...current };
      for (const id of Object.keys(STP_DATASETS)) next[id] = stamp;
      return next;
    });
  };

  const openLog = (row: StpRow) => {
    const stand = STANDS.find((s) => s.id === row.standId);
    if (!stand) return;
    setLogTarget({
      stand,
      item: {
        title: `${row.testCode} · ${row.testTitle}`,
        state: row.status === "in_progress" ? "running" : row.status === "done" ? "done" : "failed",
        meta: `${row.mode} · ${row.kernel} · СТП ${rcId}`,
        log: `/logs/${stand.name}/${row.testCode.toLowerCase()}.txt`,
      },
    });
  };

  const lastUpdated = updatedAt[rcId];

  return (
    <div className="grid gap-4">
      <div className="alert-warn text-xs">
        <ExternalLink className="w-3.5 h-3.5 shrink-0" />
        <span>
          СТП — зеркало внешней системы Jira Zephyr Scale (сегодня через Confluence, в перспективе — напрямую через Zephyr REST API).
          emm не является источником истины для этих данных: редактировать статусы тест-кейсов здесь нельзя, только смотреть и переходить в логи/Confluence.
        </span>
      </div>

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold">СТП — состав тестового прогона</h2>
          <div className="text-sm text-dim mt-1">Матрица результатов: тест-кейс × ядро × режим защищённости × стенд, для одного РЦ</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <a href={ZEPHYR_CYCLE_URL} target="_blank" rel="noreferrer" className="btn btn-sm inline-flex items-center gap-2">
            <ExternalLink className="w-4 h-4" />
            Открыть цикл в Zephyr
          </a>
          <a
            href={`${CONFLUENCE_URL}${encodeURIComponent(rcId)}`}
            target="_blank"
            rel="noreferrer"
            className="btn btn-sm inline-flex items-center gap-2"
          >
            <ExternalLink className="w-4 h-4" />
            Открыть отчёт в Confluence
          </a>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3">
        <Stat title="Комбинаций всего" value={String(totals.total)} icon={ListChecks} />
        <Stat title="Выполнено" value={String(totals.done)} icon={CheckCircle2} kind="ok" />
        <Stat title="Провалено" value={String(totals.failed)} icon={XCircle} kind="danger" />
        <Stat title="Выполняется" value={String(totals.inProgress)} icon={Loader2} kind="warn" />
        <Stat title="Не запускался" value={String(totals.notRun)} icon={Clock3} />
      </div>

      <div className="surface border border-token rounded p-3 flex items-center gap-2 flex-wrap">
        {RC_IDS.map((id) => (
          <button
            key={id}
            type="button"
            className={`btn btn-sm mono ${rcId === id ? "btn-primary" : ""}`}
            onClick={() => setRcId(id)}
            title={STP_DATASETS[id] ? undefined : "СТП для этого РЦ ещё не собиралась"}
          >
            {id}
            {!STP_DATASETS[id] && <span className="ml-1.5 text-[10px] opacity-70">нет данных</span>}
          </button>
        ))}
      </div>

      <div className="surface border border-token rounded overflow-hidden">
        <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
          <div>
            <div className="text-sm font-medium">СТП · {rcId}</div>
            <div className="text-xs text-dim">
              {lastUpdated ? `обновлено ${formatElapsed(now - lastUpdated)} назад` : "ещё не обновлялась"}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button type="button" className="btn btn-sm inline-flex items-center gap-2" onClick={refreshOne} disabled={!rows.length}>
              <RefreshCw className="w-4 h-4" />
              Обновить СТП
            </button>
            <button type="button" className="btn btn-sm inline-flex items-center gap-2" onClick={refreshAll}>
              <RefreshCcw className="w-4 h-4" />
              Обновить все СТП
            </button>
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="p-8 text-center text-dim text-sm">
            СТП для {rcId} ещё не собиралась. В legacy-системе это ручной вызов «обновить СТП» по конкретному РЦ.
          </div>
        ) : (
          <div className="overflow-auto max-h-[560px]">
            <table className="mini">
              <thead>
                <tr>
                  <SortableTh label="Тест" column="testCode" sort={sort} onSort={onSort} />
                  <SortableTh label="Ядро" column="kernel" sort={sort} onSort={onSort} />
                  <SortableTh label="Режим" column="mode" sort={sort} onSort={onSort} />
                  <SortableTh label="Стенд" column="standName" sort={sort} onSort={onSort} />
                  <SortableTh label="Статус" column="status" sort={sort} onSort={onSort} />
                  <th>Лог</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => {
                  const meta = STATUS_META[row.status];
                  const Icon = meta.icon;
                  return (
                    <tr key={row.id}>
                      <td>
                        <div className="mono text-xs text-dim">{row.testCode}</div>
                        <div className="truncate max-w-[280px]" title={row.testTitle}>{row.testTitle}</div>
                      </td>
                      <td className="mono text-xs">{row.kernel}</td>
                      <td className="mono text-xs">{row.mode}</td>
                      <td className="mono text-xs">{row.standName}</td>
                      <td>
                        <span className={`badge${meta.badge ? ` badge-${meta.badge}` : ""} inline-flex items-center gap-1`}>
                          <Icon className="w-3 h-3" />
                          {meta.label}
                        </span>
                      </td>
                      <td>
                        {row.status === "not_run" ? (
                          <span className="text-xs text-dim">—</span>
                        ) : (
                          <button type="button" className="btn btn-sm inline-flex items-center gap-1" onClick={() => openLog(row)}>
                            <ExternalLink className="w-3.5 h-3.5" />
                            Лог
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {logTarget && <LogViewerModal stand={logTarget.stand} item={logTarget.item} onClose={() => setLogTarget(null)} />}
    </div>
  );
}

function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds} сек`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  return `${hours} ч`;
}
