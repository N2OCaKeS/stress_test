/**
 * Раздел «СТП» — Состав Тестового Прогона, сводная таблица результатов из
 * внешней системы Jira Zephyr Scale. На настоящей Confluence-странице это
 * три склеенные таблицы: транспонированная матрица результатов (строки —
 * тест-кейсы, колонки — комбинации ядро×режим×стенд), характеристики
 * стендов (уже показаны на карточке сервера, здесь не дублируются) и сводка
 * по времени прохождения.
 *
 * Средняя панель здесь — не просто фильтр, а список всех версий/РЦ (раньше
 * жил отдельной вкладкой «РЦ», которую владелец решил убрать: сама эта
 * панель закрывает ту задачу). Левая панель — группировка по минорной ветке.
 * Правая рабочая зона — две таблицы выбранной версии: статус тестирования и
 * тайминг, обе сортируемые по любой колонке.
 *
 * emm здесь не источник истины: сегодня legacy публикует таблицу на
 * Confluence (`life.astralinux.ru`) вручную по кнопке, без крона. Редактировать
 * статусы тест-кейсов отсюда нельзя, только смотреть и переходить в лог/Confluence.
 */
import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock3,
  ExternalLink,
  Layers,
  ListChecks,
  Loader2,
  Search,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { naturalCompare } from "@/lib/naturalSort";
import {
  LogViewerModal,
  OS_VERSIONS,
  SortableTh,
  Stat,
  STANDS,
  useSortableRows,
  type OsVersion,
  type OsVersionStatus,
  type BadgeKind,
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
  /** false — лог существует, но ротирован (старая РЦ), показываем текст вместо ссылки */
  logAvailable: boolean;
  /** время выполнения в секундах, только для завершённых (done/failed) */
  seconds: number | null;
}

type StpColumn = "testCode" | "kernel" | "mode" | "standName" | "status";

const STATUS_META: Record<StpStatus, { label: string; icon: LucideIcon; badge?: "ok" | "danger" | "warn" }> = {
  not_run: { label: "Не запускался", icon: Clock3 },
  in_progress: { label: "Выполняется", icon: Loader2, badge: "warn" },
  done: { label: "Выполнено", icon: CheckCircle2, badge: "ok" },
  failed: { label: "Провалено", icon: XCircle, badge: "danger" },
};

const OS_STATUS_META: Record<OsVersionStatus, { label: string; badge?: BadgeKind }> = {
  active: { label: "Активна", badge: "accent" },
  testing: { label: "На тестировании", badge: "warn" },
  released: { label: "Выпущена", badge: "ok" },
  archived: { label: "Архив" },
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

/** Полный каталог тестов — для гипотетической «полной таблицы» тайминга включает и то, что для этой версии не запускалось. */
const FULL_TEST_CATALOG = [
  ...STP_TEST_CASES,
  ...STP_TEST_CASES_HOTFIX,
  { code: "ASTRA-T401", title: "Отказоустойчивость программного RAID" },
  { code: "ASTRA-T402", title: "UEFI Secure Boot: проверка цепочки доверия" },
];

const STP_MODES: StpMode[] = ["orel", "smolensk"];

type StpPattern = "mostly_done" | "in_progress" | "hotfix";

function durationSeconds(seed: number): number {
  return 200 + ((seed * 4111) % 130000);
}

function formatDuration(totalSeconds: number): string {
  const days = Math.floor(totalSeconds / 86400);
  const rest = totalSeconds % 86400;
  const hours = Math.floor(rest / 3600);
  const minutes = Math.floor((rest % 3600) / 60);
  const seconds = rest % 60;
  const hms = `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return days > 0 ? `${days} day ${hms}` : hms;
}

/**
 * Демо-матрица результатов для одной версии — тест-кейс × ядро × режим ×
 * стенд, с распределением статусов по паттерну, зависящему от статуса
 * версии (свежая ветка ещё тестируется, старая почти вся зелёная).
 * Часть завершённых строк намеренно помечена без лога (`logAvailable:
 * false`) — демонстрирует состояние «слишком старая РЦ, лог ротирован».
 */
function buildStpDataset(version: OsVersion, versionIndex: number): StpRow[] {
  const pattern: StpPattern =
    version.kind === "urgent" ? "hotfix" : version.status === "testing" ? "in_progress" : "mostly_done";
  const testCases = version.kind === "urgent" ? STP_TEST_CASES_HOTFIX : STP_TEST_CASES;
  const online = STANDS.filter((s) => s.status !== "offline");
  const offset = (versionIndex * 2) % online.length;
  const stands = Array.from({ length: 3 }, (_, k) => online[(offset + k) % online.length]);
  const kernels = version.kernels.length ? version.kernels : ["6.12.24-1.el11"];
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
          else if (pattern === "in_progress")
            status = seed < 3 ? "done" : seed < 5 ? "in_progress" : seed === 5 ? "failed" : "not_run";
          else status = seed < 8 ? "done" : "failed";
          const finished = status === "done" || status === "failed";
          rows.push({
            id: `${version.id}-${test.code}-${kernel}-${mode}-${stand.id}`,
            testCode: test.code,
            testTitle: test.title,
            kernel,
            mode,
            standName: stand.name,
            standId: stand.id,
            status,
            logAvailable: status !== "not_run" && seed % 5 !== 3,
            seconds: finished ? durationSeconds(i * 7 + versionIndex * 13) : null,
          });
        }
      }
    }
  }
  return rows;
}

const STP_DATASETS: Record<string, StpRow[]> = Object.fromEntries(
  OS_VERSIONS.map((version, index) => [version.id, buildStpDataset(version, index)]),
);

function stpValue(row: StpRow, column: StpColumn): string | number {
  return row[column];
}

const CONFLUENCE_URL = "https://life.astralinux.ru/display/DEVQA/";

// ── группировка версий по минорной ветке (1.7.x / 1.8.x) ───────────────────

function branchKey(build: string): string {
  const match = build.match(/^(\d+)\.(\d+)/);
  return match ? `${match[1]}.${match[2]}` : build;
}

interface VersionBranch {
  key: string;
  label: string;
  items: OsVersion[];
}

function groupByBranch(list: OsVersion[]): VersionBranch[] {
  const order: string[] = [];
  const map = new Map<string, OsVersion[]>();
  for (const version of list) {
    const key = branchKey(version.build);
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key)!.push(version);
  }
  return order.map((key) => ({ key, label: `${key}.x`, items: map.get(key)! }));
}

export function StpWorkzone() {
  const allBranches = useMemo(() => groupByBranch(OS_VERSIONS), []);
  const [category, setCategory] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [openBranches, setOpenBranches] = useState<Set<string>>(() => new Set(allBranches.map((b) => b.key)));
  const [selectedId, setSelectedId] = useState<string>(OS_VERSIONS[0].id);

  const filteredVersions = useMemo(() => {
    const term = search.trim().toLowerCase();
    return OS_VERSIONS.filter((v) => {
      if (category !== "all" && branchKey(v.build) !== category) return false;
      if (!term) return true;
      return v.id.toLowerCase().includes(term);
    });
  }, [search, category]);

  const groups = useMemo(() => {
    return groupByBranch(filteredVersions).map((group) => ({
      ...group,
      items: [...group.items].sort((a, b) =>
        sortDir === "asc" ? naturalCompare(a.id, b.id) : naturalCompare(b.id, a.id),
      ),
    }));
  }, [filteredVersions, sortDir]);

  const toggleBranch = (key: string) => {
    setOpenBranches((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const version = OS_VERSIONS.find((v) => v.id === selectedId) ?? OS_VERSIONS[0];

  return (
    <div className="grid gap-4">
      <div className="alert-warn text-xs">
        <ExternalLink className="w-3.5 h-3.5 shrink-0" />
        <span>
          СТП — зеркало внешней системы Jira Zephyr Scale, сегодня публикуется на Confluence вручную по кнопке.
          emm не редактирует статусы тест-кейсов отсюда, только показывает и даёт переход в лог/Confluence.
        </span>
      </div>

      <div>
        <h2 className="text-lg font-semibold">СТП — состав тестового прогона</h2>
        <div className="text-sm text-dim mt-1">
          Выберите версию слева — справа матрица результатов и сводка по времени выполнения для неё
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[200px_320px_minmax(0,1fr)] gap-4 items-start">
        <CategoriesPanel
          branches={allBranches.map((b) => ({ key: b.key, label: b.label, count: b.items.length }))}
          active={category}
          onSelect={setCategory}
          total={OS_VERSIONS.length}
        />
        <VersionsPanel
          groups={groups}
          openBranches={openBranches}
          onToggleBranch={toggleBranch}
          selectedId={selectedId}
          onSelect={setSelectedId}
          search={search}
          onSearch={setSearch}
          sortDir={sortDir}
          onToggleSort={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
          total={OS_VERSIONS.length}
        />
        <RightWorkzone version={version} />
      </div>
    </div>
  );
}

// ── левая панель: категории (ветки) ─────────────────────────────────────────

function CategoriesPanel({
  branches,
  active,
  onSelect,
  total,
}: {
  branches: { key: string; label: string; count: number }[];
  active: string;
  onSelect: (key: string) => void;
  total: number;
}) {
  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center gap-2">
        <Layers className="w-4 h-4 text-accent" />
        <div className="text-sm font-medium">Ветки</div>
      </div>
      <div className="p-2 grid gap-1">
        <button
          type="button"
          onClick={() => onSelect("all")}
          className={`btn btn-sm w-full flex items-center justify-between ${active === "all" ? "btn-primary" : ""}`}
        >
          <span>Все версии</span>
          <span className="mono text-[11px] opacity-80">{total}</span>
        </button>
        {branches.map((b) => (
          <button
            key={b.key}
            type="button"
            onClick={() => onSelect(b.key)}
            className={`btn btn-sm w-full flex items-center justify-between ${active === b.key ? "btn-primary" : ""}`}
          >
            <span className="mono">{b.label}</span>
            <span className="mono text-[11px] opacity-80">{b.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── средняя панель: список версий, сгруппированный по веткам ───────────────

function VersionsPanel({
  groups,
  openBranches,
  onToggleBranch,
  selectedId,
  onSelect,
  search,
  onSearch,
  sortDir,
  onToggleSort,
  total,
}: {
  groups: VersionBranch[];
  openBranches: Set<string>;
  onToggleBranch: (key: string) => void;
  selectedId: string;
  onSelect: (id: string) => void;
  search: string;
  onSearch: (v: string) => void;
  sortDir: "asc" | "desc";
  onToggleSort: () => void;
  total: number;
}) {
  return (
    <div className="surface border border-token rounded overflow-hidden flex flex-col max-h-[720px]">
      <div className="border-b border-token p-3 shrink-0">
        <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${total} версиям…`}
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
        </div>
        <button type="button" className="btn btn-sm w-full mt-2 flex items-center justify-center gap-2" onClick={onToggleSort}>
          {sortDir === "desc" ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          {sortDir === "desc" ? "Новые сверху" : "Старые сверху"}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {groups.length === 0 && <div className="p-6 text-center text-dim text-sm">Нет версий по фильтру</div>}
        {groups.map((group) => {
          const open = openBranches.has(group.key);
          return (
            <div key={group.key} className="border-b border-token last:border-b-0">
              <button
                type="button"
                onClick={() => onToggleBranch(group.key)}
                className="w-full flex items-center gap-2 px-3 py-2 hover-bg"
              >
                {open ? <ChevronDown className="w-4 h-4 text-dim" /> : <ChevronRight className="w-4 h-4 text-dim" />}
                <span className="font-semibold mono text-sm">{group.label}</span>
                <span className="text-xs text-dim">{group.items.length}</span>
              </button>
              {open && (
                <div>
                  {group.items.map((v) => (
                    <VersionRow key={v.id} version={v} active={v.id === selectedId} onSelect={() => onSelect(v.id)} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function VersionRow({ version, active, onSelect }: { version: OsVersion; active: boolean; onSelect: () => void }) {
  const meta = OS_STATUS_META[version.status];
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left pl-8 pr-3 py-2 hover-bg flex items-center justify-between gap-2 ${
        active ? "surface-2 border-l-2 border-accent" : "border-l-2 border-transparent"
      }`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="font-semibold mono truncate">{version.id}</span>
          {version.kind === "urgent" && <span className="badge badge-warn">хотфикс</span>}
        </div>
        <div className="text-[11px] text-dim mt-0.5">{version.createdAt}</div>
      </div>
      <span className={meta.badge ? `badge badge-${meta.badge} shrink-0` : "badge shrink-0"}>{meta.label}</span>
    </button>
  );
}

// ── правая рабочая зона: карточка версии + две таблицы ──────────────────────

function RightWorkzone({ version }: { version: OsVersion }) {
  const meta = OS_STATUS_META[version.status];
  const confluenceUrl = `${CONFLUENCE_URL}${encodeURIComponent(version.id)}`;
  return (
    <div className="grid gap-4 min-w-0">
      <div className="surface border border-token rounded p-4 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg font-semibold mono">{version.id}</span>
            <span className={meta.badge ? `badge badge-${meta.badge}` : "badge"}>{meta.label}</span>
            {version.kind === "urgent" && <span className="badge badge-warn">срочный хотфикс</span>}
          </div>
          <div className="text-xs text-dim mt-1">
            build {version.build} · rc {version.rc} · создана {version.createdAt} · ядра: {version.kernels.join(", ")}
          </div>
        </div>
        <a href={confluenceUrl} target="_blank" rel="noreferrer" className="btn btn-sm inline-flex items-center gap-2">
          <ExternalLink className="w-4 h-4" />
          Открыть СТП в Life
        </a>
      </div>

      <StpStatusTable version={version} />
      <StpTimingTable version={version} />
    </div>
  );
}

// ── таблица 1: статус тестирования ──────────────────────────────────────────

function StpStatusTable({ version }: { version: OsVersion }) {
  const rows = useMemo(() => STP_DATASETS[version.id] ?? [], [version.id]);
  const { sorted, sort, onSort } = useSortableRows<StpRow, StpColumn>(rows, stpValue, { column: "testCode", dir: "asc" });
  const [logTarget, setLogTarget] = useState<{ stand: Stand; item: QueueItem } | null>(null);

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

  const openLog = (row: StpRow) => {
    const stand = STANDS.find((s) => s.id === row.standId);
    if (!stand) return;
    setLogTarget({
      stand,
      item: {
        title: `${row.testCode} · ${row.testTitle}`,
        state: row.status === "in_progress" ? "running" : row.status === "done" ? "done" : "failed",
        meta: `${row.mode} · ${row.kernel} · СТП ${version.id}`,
        log: `/logs/${stand.name}/${row.testCode.toLowerCase()}.txt`,
      },
    });
  };

  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat title="Комбинаций всего" value={String(totals.total)} icon={ListChecks} />
        <Stat title="Выполнено" value={String(totals.done)} icon={CheckCircle2} kind="ok" />
        <Stat title="Провалено" value={String(totals.failed)} icon={XCircle} kind="danger" />
        <Stat title="Выполняется" value={String(totals.inProgress)} icon={Loader2} kind="warn" />
        <Stat title="Не запускался" value={String(totals.notRun)} icon={Clock3} />
      </div>

      <div className="surface border border-token rounded overflow-hidden">
        <div className="border-b border-token p-3 text-sm font-medium">Статус тестирования · {version.id}</div>
        <div className="overflow-auto max-h-[420px]">
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
                const rowMeta = STATUS_META[row.status];
                const Icon = rowMeta.icon;
                return (
                  <tr key={row.id}>
                    <td>
                      <div className="mono text-xs text-dim">{row.testCode}</div>
                      <div className="truncate max-w-[260px]" title={row.testTitle}>
                        {row.testTitle}
                      </div>
                    </td>
                    <td className="mono text-xs">{row.kernel}</td>
                    <td className="mono text-xs">{row.mode}</td>
                    <td className="mono text-xs">{row.standName}</td>
                    <td>
                      <span className={`badge${rowMeta.badge ? ` badge-${rowMeta.badge}` : ""} inline-flex items-center gap-1`}>
                        <Icon className="w-3 h-3" />
                        {rowMeta.label}
                      </span>
                    </td>
                    <td>
                      {row.status === "not_run" ? (
                        <span className="text-xs text-dim">—</span>
                      ) : row.logAvailable ? (
                        <button type="button" className="btn btn-sm inline-flex items-center gap-1" onClick={() => openLog(row)}>
                          <ExternalLink className="w-3.5 h-3.5" />
                          Лог
                        </button>
                      ) : (
                        <span className="text-xs text-dim italic">Слишком старая РЦ, лог ротирован</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {logTarget && <LogViewerModal stand={logTarget.stand} item={logTarget.item} onClose={() => setLogTarget(null)} />}
    </div>
  );
}

// ── таблица 2: тайминг ──────────────────────────────────────────────────────

type TimingScope = "run" | "full";

interface TimingRow {
  id: string;
  testCode: string;
  testTitle: string;
  kernel: string;
  mode: StpMode;
  standName: string;
  seconds: number | null;
}

type TimingColumn = "testCode" | "kernel" | "mode" | "standName" | "seconds";

function timingValue(row: TimingRow, column: TimingColumn): string | number {
  if (column === "seconds") return row.seconds ?? -1;
  return row[column];
}

/** Гипотетическая полная матрица: весь каталог тестов на нескольких стендах для обоих режимов и всех ядер версии. Большинство ячеек пустые — так и задумано, это витрина охвата, а не факт. */
function buildFullTimingMatrix(version: OsVersion, ranRows: TimingRow[]): TimingRow[] {
  const lookup = new Map<string, number>();
  for (const r of ranRows) {
    if (r.seconds != null) lookup.set(`${r.testCode}|${r.kernel}|${r.mode}|${r.standName}`, r.seconds);
  }
  const stands = STANDS.filter((s) => s.status !== "offline").slice(0, 5);
  const rows: TimingRow[] = [];
  for (const test of FULL_TEST_CATALOG) {
    for (const kernel of version.kernels) {
      for (const mode of STP_MODES) {
        for (const stand of stands) {
          const key = `${test.code}|${kernel}|${mode}|${stand.name}`;
          rows.push({
            id: `full-${version.id}-${key}`,
            testCode: test.code,
            testTitle: test.title,
            kernel,
            mode,
            standName: stand.name,
            seconds: lookup.has(key) ? lookup.get(key)! : null,
          });
        }
      }
    }
  }
  return rows;
}

function StpTimingTable({ version }: { version: OsVersion }) {
  const [scope, setScope] = useState<TimingScope>("run");

  const ranRows: TimingRow[] = useMemo(
    () =>
      (STP_DATASETS[version.id] ?? [])
        .filter((r) => r.seconds != null)
        .map((r) => ({
          id: r.id,
          testCode: r.testCode,
          testTitle: r.testTitle,
          kernel: r.kernel,
          mode: r.mode,
          standName: r.standName,
          seconds: r.seconds,
        })),
    [version.id],
  );
  const fullRows = useMemo(() => buildFullTimingMatrix(version, ranRows), [version, ranRows]);
  const rows = scope === "run" ? ranRows : fullRows;

  const { sorted, sort, onSort } = useSortableRows<TimingRow, TimingColumn>(rows, timingValue, {
    column: "testCode",
    dir: "asc",
  });

  const emptyCount = scope === "full" ? rows.filter((r) => r.seconds == null).length : 0;

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-sm font-medium">Тайминг · {version.id}</div>
          <div className="text-xs text-dim">
            {scope === "run"
              ? `${sorted.length} строк — только реально запущенные комбинации`
              : `${sorted.length} строк, из них пустых: ${emptyCount} — гипотетическое покрытие всеми тестами на всех стендах`}
          </div>
        </div>
        <div className="surface-2 border border-token rounded p-1 flex items-center gap-1">
          <button type="button" className={`btn btn-sm ${scope === "run" ? "btn-primary" : ""}`} onClick={() => setScope("run")}>
            Только тесты этого РЦ
          </button>
          <button type="button" className={`btn btn-sm ${scope === "full" ? "btn-primary" : ""}`} onClick={() => setScope("full")}>
            Полная таблица
          </button>
        </div>
      </div>
      <div className="overflow-auto max-h-[420px]">
        <table className="mini">
          <thead>
            <tr>
              <SortableTh label="Тест" column="testCode" sort={sort} onSort={onSort} />
              <SortableTh label="Ядро" column="kernel" sort={sort} onSort={onSort} />
              <SortableTh label="Режим" column="mode" sort={sort} onSort={onSort} />
              <SortableTh label="Стенд" column="standName" sort={sort} onSort={onSort} />
              <SortableTh label="Время выполнения" column="seconds" sort={sort} onSort={onSort} />
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.id}>
                <td>
                  <div className="mono text-xs text-dim">{row.testCode}</div>
                  <div className="truncate max-w-[260px]" title={row.testTitle}>
                    {row.testTitle}
                  </div>
                </td>
                <td className="mono text-xs">{row.kernel}</td>
                <td className="mono text-xs">{row.mode}</td>
                <td className="mono text-xs">{row.standName}</td>
                <td className="mono text-xs">{row.seconds == null ? "—" : formatDuration(row.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
