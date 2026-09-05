/**
 * Раздел «СТП» — Состав Тестового Прогона, сводная таблица результатов из
 * внешней системы Jira Zephyr Scale. На настоящей Confluence-странице это
 * транспонированная матрица: строки — тест-кейсы, колонки — комбинации
 * ядро×режим×стенд, плюс отдельная таблица тайминга прохождения.
 *
 * Средняя панель — список всех версий/РЦ, сгруппированный по минорной ветке
 * (1.7.x/1.8.x), со сворачиваемыми группами; используется как `middle` в
 * `Shell` по тому же паттерну, что и `pages/server/Server.tsx` (поиск+сорт
 * сверху не скроллятся, список версий скроллится, кнопка снизу закреплена).
 * Состояние этой панели общее с рабочей зоной — обе стороны получают его из
 * `useStpVersionState`, вызываемого один раз в `Testing.tsx`.
 *
 * emm здесь не источник истины: сегодня legacy публикует таблицу на
 * Confluence (`life.astralinux.ru`) вручную по кнопке, без крона. Редактировать
 * статусы тест-кейсов отсюда нельзя, только смотреть и переходить в лог/Confluence.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ExternalLink,
  FilterX,
  ListChecks,
  RefreshCcw,
  Search,
} from "lucide-react";
import { naturalCompare } from "@/lib/naturalSort";
import { useToast } from "@/contexts/ToastContext";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import {
  LogViewerModal,
  ModalHeader,
  OS_VERSIONS,
  STANDS,
  type BadgeKind,
  type OsVersion,
  type OsVersionStatus,
  type QueueItem,
  type Stand,
} from "./_shared";

export type StpStatus = "not_run" | "in_progress" | "done" | "failed";
export type StpMode = "orel" | "smolensk";

interface StpCombo {
  idx: number;
  kernel: string;
  mode: StpMode;
  standName: string;
  standId: number;
}

interface StpCell {
  status: StpStatus;
  /** false — лог существует, но ротирован (старая РЦ), показываем текст вместо ссылки */
  logAvailable: boolean;
  /** время выполнения в секундах, только для завершённых (done/failed) */
  seconds: number | null;
}

interface StpTestCase {
  code: string;
  title: string;
}

interface StpDataset {
  testCases: StpTestCase[];
  combos: StpCombo[];
  cells: Map<string, StpCell>;
}

const STATUS_META: Record<StpStatus, { label: string }> = {
  not_run: { label: "Не запускался" },
  in_progress: { label: "Выполняется" },
  done: { label: "Выполнено" },
  failed: { label: "Провалено" },
};

const STATUS_ORDER: StpStatus[] = ["done", "in_progress", "failed", "not_run"];

const OS_STATUS_META: Record<OsVersionStatus, { label: string; badge?: BadgeKind }> = {
  active: { label: "Активна", badge: "accent" },
  testing: { label: "На тестировании", badge: "warn" },
  released: { label: "Выпущена", badge: "ok" },
  archived: { label: "Архив" },
};

const STP_TEST_CASES: StpTestCase[] = [
  { code: "ASTRA-T101", title: "Установка с загрузочного носителя" },
  { code: "ASTRA-T102", title: "Настройка режима Смоленск (МРД+МКЦ)" },
  { code: "ASTRA-T103", title: "Присоединение к домену FreeIPA" },
  { code: "ASTRA-T104", title: "PostgreSQL: базовое резервное копирование" },
  { code: "ASTRA-T105", title: "Сетевой стек: iptables + nftables совместимость" },
  { code: "ASTRA-T106", title: "Аудит: пересылка событий в syslog" },
];

const STP_TEST_CASES_HOTFIX: StpTestCase[] = [
  { code: "ASTRA-T301", title: "Хотфикс: регресс сетевого драйвера" },
  { code: "ASTRA-T302", title: "Хотфикс: проверка совместимости с предыдущим ядром" },
];

/** Полный каталог тестов — для гипотетической «полной таблицы» тайминга включает и то, что для этой версии не запускалось. */
const FULL_TEST_CATALOG: StpTestCase[] = [
  ...STP_TEST_CASES,
  ...STP_TEST_CASES_HOTFIX,
  { code: "ASTRA-T401", title: "Отказоустойчивость программного RAID" },
  { code: "ASTRA-T402", title: "UEFI Secure Boot: проверка цепочки доверия" },
];

const STP_MODES: StpMode[] = ["orel", "smolensk"];

const CONFLUENCE_URL = "https://life.astralinux.ru/display/DEVQA/";

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
 * Демо-матрица результатов для одной версии — тест-кейс × комбинация
 * ядро×режим×стенд, с распределением статусов по паттерну, зависящему от
 * статуса версии (свежая ветка ещё тестируется, старая почти вся зелёная).
 * Часть завершённых ячеек намеренно помечена без лога (`logAvailable:
 * false`, около 20% через детерминированный seed) — демонстрирует состояние
 * «слишком старая РЦ, лог ротирован».
 */
function buildStpDataset(version: OsVersion, versionIndex: number): StpDataset {
  const pattern: StpPattern =
    version.kind === "urgent" ? "hotfix" : version.status === "testing" ? "in_progress" : "mostly_done";
  const testCases = version.kind === "urgent" ? STP_TEST_CASES_HOTFIX : STP_TEST_CASES;
  const online = STANDS.filter((s) => s.status !== "offline");
  const offset = (versionIndex * 2) % online.length;
  const stands = Array.from({ length: 3 }, (_, k) => online[(offset + k) % online.length]);
  const kernels = version.kernels.length ? version.kernels : ["6.12.24-1.el11"];

  const combos: StpCombo[] = [];
  let idx = 0;
  for (const mode of STP_MODES) {
    for (const kernel of kernels) {
      for (const stand of stands) {
        combos.push({ idx, kernel, mode, standName: stand.name, standId: stand.id });
        idx += 1;
      }
    }
  }

  const cells = new Map<string, StpCell>();
  let i = 0;
  for (const test of testCases) {
    for (const combo of combos) {
      i += 1;
      const seed = i % 10;
      let status: StpStatus;
      if (pattern === "mostly_done") status = seed === 0 ? "failed" : seed === 1 ? "in_progress" : "done";
      else if (pattern === "in_progress")
        status = seed < 3 ? "done" : seed < 5 ? "in_progress" : seed === 5 ? "failed" : "not_run";
      else status = seed < 8 ? "done" : "failed";
      const finished = status === "done" || status === "failed";
      cells.set(`${test.code}|${combo.idx}`, {
        status,
        logAvailable: status !== "not_run" && seed % 5 !== 3,
        seconds: finished ? durationSeconds(i * 7 + versionIndex * 13) : null,
      });
    }
  }
  return { testCases, combos, cells };
}

const STP_DATASETS: Record<string, StpDataset> = Object.fromEntries(
  OS_VERSIONS.map((version, index) => [version.id, buildStpDataset(version, index)]),
);

function countByStatus(dataset: StpDataset, status: StpStatus): number {
  let count = 0;
  for (const cell of dataset.cells.values()) if (cell.status === status) count += 1;
  return count;
}

const STP_FAILED_COUNTS: Record<string, number> = Object.fromEntries(
  Object.entries(STP_DATASETS).map(([id, dataset]) => [id, countByStatus(dataset, "failed")]),
);

const UPDATED_AGO_DEMO = [
  "4 мин назад",
  "1 день назад",
  "3 дня назад",
  "5 дней назад",
  "2 дня назад",
  "6 дней назад",
  "12 дней назад",
  "19 дней назад",
  "1 мес назад",
];
const STP_UPDATED_AGO: Record<string, string> = Object.fromEntries(
  OS_VERSIONS.map((v, i) => [v.id, UPDATED_AGO_DEMO[i % UPDATED_AGO_DEMO.length]]),
);

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

// ── состояние средней панели, общее для StpMiddlePanel и StpWorkzone ───────

export interface StpVersionState {
  groups: VersionBranch[];
  openBranches: Set<string>;
  toggleBranch: (key: string) => void;
  search: string;
  setSearch: (v: string) => void;
  sortDir: "asc" | "desc";
  toggleSort: () => void;
  selectedId: string;
  setSelectedId: (id: string) => void;
  version: OsVersion;
  total: number;
}

export function useStpVersionState(): StpVersionState {
  const allBranches = useMemo(() => groupByBranch(OS_VERSIONS), []);
  const [search, setSearch] = useState("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [openBranches, setOpenBranches] = useState<Set<string>>(() => new Set(allBranches.map((b) => b.key)));
  const [selectedId, setSelectedId] = useState<string>(OS_VERSIONS[0].id);

  const groups = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = term ? OS_VERSIONS.filter((v) => v.id.toLowerCase().includes(term)) : OS_VERSIONS;
    return groupByBranch(filtered).map((group) => ({
      ...group,
      items: [...group.items].sort((a, b) =>
        sortDir === "asc" ? naturalCompare(a.id, b.id) : naturalCompare(b.id, a.id),
      ),
    }));
  }, [search, sortDir]);

  const toggleBranch = (key: string) => {
    setOpenBranches((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const version = OS_VERSIONS.find((v) => v.id === selectedId) ?? OS_VERSIONS[0];

  return {
    groups,
    openBranches,
    toggleBranch,
    search,
    setSearch,
    sortDir,
    toggleSort: () => setSortDir((d) => (d === "asc" ? "desc" : "asc")),
    selectedId,
    setSelectedId,
    version,
    total: OS_VERSIONS.length,
  };
}

// ── средняя панель Shell: список версий, сгруппированный по веткам ─────────

export function StpMiddlePanel({ state }: { state: StpVersionState }) {
  const toast = useToast();
  return (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${state.total} версиям…`}
            value={state.search}
            onChange={(e) => state.setSearch(e.target.value)}
          />
        </div>
        <button
          type="button"
          className="btn btn-sm w-full mt-2 flex items-center justify-center gap-2"
          onClick={state.toggleSort}
        >
          {state.sortDir === "desc" ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          {state.sortDir === "desc" ? "Новые сверху" : "Старые сверху"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {state.groups.length === 0 && <div className="px-3 py-6 text-xs text-dim text-center">Нет версий по фильтру</div>}
        {state.groups.map((group) => {
          const open = state.openBranches.has(group.key);
          return (
            <div key={group.key}>
              <button
                type="button"
                onClick={() => state.toggleBranch(group.key)}
                className="w-full flex items-center gap-2 px-3 py-1.5 hover-bg"
              >
                {open ? <ChevronDown className="w-3.5 h-3.5 text-dim" /> : <ChevronRight className="w-3.5 h-3.5 text-dim" />}
                <span className="font-semibold mono text-xs uppercase tracking-wide text-dim">Ветка {group.label}</span>
                <span className="text-[11px] text-dim ml-auto">{group.items.length}</span>
              </button>
              {open && (
                <div>
                  {group.items.map((v) => (
                    <VersionRow
                      key={v.id}
                      version={v}
                      active={v.id === state.selectedId}
                      onSelect={() => state.setSelectedId(v.id)}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="border-t border-token p-3 shrink-0">
        <button
          type="button"
          className="btn btn-sm w-full flex items-center justify-center gap-2"
          onClick={() =>
            toast.info(
              "Демо: запущено бы обновление СТП для всех РЦ разом (в отличие от legacy allta_app, где обновление всегда только по одному РЦ)",
            )
          }
        >
          <RefreshCcw className="w-3.5 h-3.5" />
          Обновить СТП всех РЦ
        </button>
      </div>
    </aside>
  );
}

function VersionRow({ version, active, onSelect }: { version: OsVersion; active: boolean; onSelect: () => void }) {
  const meta = OS_STATUS_META[version.status];
  const failed = STP_FAILED_COUNTS[version.id] ?? 0;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left pl-8 pr-3 py-2 hover-bg flex items-center gap-2 border-l-2 ${
        active ? "surface-2 border-accent" : "border-transparent"
      }`}
    >
      <span className="font-semibold mono text-sm truncate">{version.id}</span>
      {version.kind === "urgent" && <span className="badge badge-warn shrink-0">хотфикс</span>}
      <span className={`text-[11px] mono ml-auto shrink-0 ${failed > 0 ? "text-danger" : "text-dim"}`}>
        {failed > 0 ? `${failed} ✕` : "—"}
      </span>
      <span className={meta.badge ? `badge badge-${meta.badge} shrink-0` : "badge shrink-0"}>{meta.label}</span>
    </button>
  );
}

// ── рабочая зона: карточка версии + две таблицы ─────────────────────────────

export function StpWorkzone({ state }: { state: StpVersionState }) {
  const { version } = state;
  const dataset = STP_DATASETS[version.id];
  const [refreshedIds, setRefreshedIds] = useState<Set<string>>(new Set());
  const updatedLabel = refreshedIds.has(version.id) ? "обновлено только что" : STP_UPDATED_AGO[version.id];

  const [filters, setFilters] = useState<StpSharedFilters>(emptySharedFilters);
  const [statusFilter, setStatusFilter] = useState<Set<string>>(new Set());
  // Смена РЦ в средней панели даёт другой набор стендов/ядер — старый выбор
  // фильтров может не иметь смысла для новой версии, поэтому сбрасываем.
  useEffect(() => {
    setFilters(emptySharedFilters());
    setStatusFilter(new Set());
  }, [version.id]);

  const fullStands = useMemo(() => STANDS.filter((s) => s.status !== "offline").slice(0, 5), []);
  const fullKernels = useMemo(() => (version.kernels.length ? version.kernels : ["6.12.24-1.el11"]), [version.kernels]);
  const fullCombos: StpCombo[] = useMemo(() => {
    const combos: StpCombo[] = [];
    let idx = 0;
    for (const mode of STP_MODES) {
      for (const kernel of fullKernels) {
        for (const stand of fullStands) {
          combos.push({ idx, kernel, mode, standName: stand.name, standId: stand.id });
          idx += 1;
        }
      }
    }
    return combos;
  }, [fullStands, fullKernels]);

  // Опции фильтра — объединение реальных комбинаций этого РЦ и гипотетической
  // полной таблицы тайминга, чтобы панель покрывала то, что может показать
  // любая из двух таблиц.
  const standOptions: DropdownOption[] = useMemo(() => {
    const names = new Set([...dataset.combos.map((c) => c.standName), ...fullCombos.map((c) => c.standName)]);
    return Array.from(names).map((v) => ({ value: v, label: v }));
  }, [dataset, fullCombos]);
  const kernelOptions: DropdownOption[] = useMemo(() => {
    const kernels = new Set([...dataset.combos.map((c) => c.kernel), ...fullCombos.map((c) => c.kernel)]);
    return Array.from(kernels).map((v) => ({ value: v, label: v }));
  }, [dataset, fullCombos]);

  return (
    <div className="grid gap-4 min-w-0">
      <div className="alert-warn text-xs">
        <ExternalLink className="w-3.5 h-3.5 shrink-0" />
        <span>
          СТП — зеркало внешней системы Jira Zephyr Scale, сегодня публикуется на Confluence вручную по кнопке.
          emm не редактирует статусы тест-кейсов отсюда, только показывает и даёт переход в лог/Confluence.
        </span>
      </div>

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg font-semibold mono">{version.id}</span>
            <span className="text-xs text-dim">
              РЦ · Astra Linux SE {branchKey(version.build)} · {dataset.combos.length} комбинаций ядро×режим×стенд
            </span>
          </div>
          <div className="text-xs text-dim mt-1">
            build {version.build} · rc {version.rc} · создана {version.createdAt} · ядра: {version.kernels.join(", ")}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-dim">{updatedLabel}</span>
          <button
            type="button"
            className="btn btn-sm inline-flex items-center gap-2"
            onClick={() => setRefreshedIds((current) => new Set(current).add(version.id))}
          >
            <RefreshCcw className="w-3.5 h-3.5" />
            Обновить СТП
          </button>
        </div>
      </div>

      <StpFilterBar
        filters={filters}
        onFiltersChange={setFilters}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        standOptions={standOptions}
        kernelOptions={kernelOptions}
      />

      <StpStatusTable version={version} dataset={dataset} filters={filters} statusFilter={statusFilter} />
      <StpTimingTable version={version} dataset={dataset} filters={filters} fullCombos={fullCombos} />
    </div>
  );
}

// ── фильтры, общие для обеих таблиц ─────────────────────────────────────────

/**
 * Измерения, по которым фильтруются одновременно и статус, и тайминг —
 * стенд/режим/ядро/тест. Статус результата актуален только для таблицы
 * статуса (в тайминге такой колонки нет), поэтому живёт отдельным набором
 * рядом, а не внутри этого типа.
 */
export interface StpSharedFilters {
  stand: Set<string>;
  mode: Set<string>;
  kernel: Set<string>;
  test: Set<string>;
}

function emptySharedFilters(): StpSharedFilters {
  return { stand: new Set(), mode: new Set(), kernel: new Set(), test: new Set() };
}

function matchesCombo(filters: StpSharedFilters, c: StpCombo): boolean {
  return (
    (filters.stand.size === 0 || filters.stand.has(c.standName)) &&
    (filters.mode.size === 0 || filters.mode.has(c.mode)) &&
    (filters.kernel.size === 0 || filters.kernel.has(c.kernel))
  );
}

function StpFilterBar({
  filters,
  onFiltersChange,
  statusFilter,
  onStatusFilterChange,
  standOptions,
  kernelOptions,
}: {
  filters: StpSharedFilters;
  onFiltersChange: (next: StpSharedFilters) => void;
  statusFilter: Set<string>;
  onStatusFilterChange: (next: Set<string>) => void;
  standOptions: DropdownOption[];
  kernelOptions: DropdownOption[];
}) {
  const modeOptions: DropdownOption[] = STP_MODES.map((m) => ({ value: m, label: m }));
  const testOptions: DropdownOption[] = FULL_TEST_CATALOG.map((t) => ({ value: t.code, label: `${t.code} · ${t.title}` }));
  const statusOptions: DropdownOption[] = STATUS_ORDER.map((s) => ({ value: s, label: STATUS_META[s].label }));

  const hasAny =
    filters.stand.size > 0 || filters.mode.size > 0 || filters.kernel.size > 0 || filters.test.size > 0 || statusFilter.size > 0;

  const reset = () => {
    onFiltersChange(emptySharedFilters());
    onStatusFilterChange(new Set());
  };

  return (
    <div className="surface border border-token rounded p-3 flex items-center gap-2 flex-wrap">
      <span className="text-xs text-dim font-medium">Фильтры (общие для обеих таблиц):</span>
      <Dropdown
        mode="multi"
        label="Стенд"
        searchable
        options={standOptions}
        value={filters.stand}
        onChange={(v) => onFiltersChange({ ...filters, stand: v })}
      />
      <Dropdown
        mode="multi"
        label="Режим"
        options={modeOptions}
        value={filters.mode}
        onChange={(v) => onFiltersChange({ ...filters, mode: v })}
      />
      <Dropdown
        mode="multi"
        label="Ядро"
        searchable
        options={kernelOptions}
        value={filters.kernel}
        onChange={(v) => onFiltersChange({ ...filters, kernel: v })}
      />
      <Dropdown
        mode="multi"
        label="Тест"
        searchable
        options={testOptions}
        value={filters.test}
        onChange={(v) => onFiltersChange({ ...filters, test: v })}
      />
      <Dropdown
        mode="multi"
        label="Статус"
        options={statusOptions}
        value={statusFilter}
        onChange={onStatusFilterChange}
      />
      <button
        type="button"
        className="btn btn-sm ml-auto inline-flex items-center gap-1.5"
        disabled={!hasAny}
        onClick={reset}
      >
        <FilterX className="w-3.5 h-3.5" />
        Сбросить фильтры
      </button>
    </div>
  );
}

// ── таблица 1: статус тестирования (транспонированная матрица) ─────────────

interface CellTarget {
  test: StpTestCase;
  combo: StpCombo;
  cell: StpCell;
}

function StpStatusTable({
  version,
  dataset,
  filters,
  statusFilter,
}: {
  version: OsVersion;
  dataset: StpDataset;
  filters: StpSharedFilters;
  statusFilter: Set<string>;
}) {
  const [sort, setSort] = useState<{ column: "name" | "failcount" | null; dir: 1 | -1 }>({ column: null, dir: 1 });
  const [cellTarget, setCellTarget] = useState<CellTarget | null>(null);
  const [logTarget, setLogTarget] = useState<{ stand: Stand; item: QueueItem } | null>(null);

  const visibleCombos = useMemo(
    () => dataset.combos.filter((c) => matchesCombo(filters, c)),
    [dataset, filters],
  );

  const rows = useMemo(() => {
    let list = dataset.testCases.filter((test) => {
      if (filters.test.size > 0 && !filters.test.has(test.code)) return false;
      if (statusFilter.size === 0) return true;
      return visibleCombos.some((c) => statusFilter.has(dataset.cells.get(`${test.code}|${c.idx}`)!.status));
    });
    if (sort.column === "name") {
      list = [...list].sort((a, b) => naturalCompare(a.code, b.code) * sort.dir);
    } else if (sort.column === "failcount") {
      list = [...list].sort((a, b) => {
        const fa = visibleCombos.filter((c) => dataset.cells.get(`${a.code}|${c.idx}`)?.status === "failed").length;
        const fb = visibleCombos.filter((c) => dataset.cells.get(`${b.code}|${c.idx}`)?.status === "failed").length;
        return (fa - fb) * sort.dir;
      });
    }
    return list;
  }, [dataset, visibleCombos, filters.test, statusFilter, sort]);

  const setSortColumn = (column: "name" | "failcount") => {
    setSort((current) => (current.column === column ? { column, dir: current.dir === 1 ? -1 : 1 } : { column, dir: 1 }));
  };

  const findStand = (standId: number) => STANDS.find((s) => s.id === standId);

  const openLog = (test: StpTestCase, combo: StpCombo) => {
    const stand = findStand(combo.standId);
    if (!stand) return;
    setLogTarget({
      stand,
      item: {
        title: `${test.code} · ${test.title}`,
        state: "done",
        meta: `${combo.mode} · ${combo.kernel} · СТП ${version.id}`,
        log: `/logs/${stand.name}/${test.code.toLowerCase()}.txt`,
      },
    });
  };

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="text-sm font-medium flex items-center gap-2">
          <ListChecks className="w-4 h-4 text-accent" />
          Статус тестирования · {version.id}
        </div>
        <div className="flex items-center gap-3">
          <a
            href={`${CONFLUENCE_URL}${encodeURIComponent(version.id)}`}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-accent inline-flex items-center gap-1 hover:underline"
          >
            <ExternalLink className="w-3 h-3" />
            Результаты в Confluence
          </a>
          <a
            href={`${CONFLUENCE_URL}${encodeURIComponent(version.id)}`}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-accent inline-flex items-center gap-1 hover:underline"
          >
            <ExternalLink className="w-3 h-3" />
            Открыть СТП в Life
          </a>
        </div>
      </div>

      <div className="px-3 py-1.5 surface-2 border-b border-token text-[11px] text-dim">
        {visibleCombos.length} из {dataset.combos.length} комбинаций · {rows.length} из {dataset.testCases.length} тестов
      </div>

      <div className="overflow-auto max-h-[440px]">
        <table className="text-xs border-collapse w-max min-w-full">
          <thead className="sticky top-0 z-20">
            <tr>
              <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                Версия
              </th>
              {visibleCombos.map((c) => (
                <td key={c.idx} className="surface-2 border-b border-token px-2 py-1 mono text-dim whitespace-nowrap">
                  {version.id}
                </td>
              ))}
            </tr>
            <tr>
              <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                Ядро
              </th>
              {visibleCombos.map((c) => (
                <td key={c.idx} className="surface-2 border-b border-token px-2 py-1 mono text-dim whitespace-nowrap">
                  {c.kernel}
                </td>
              ))}
            </tr>
            <tr>
              <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                Режим
              </th>
              {visibleCombos.map((c) => (
                <td key={c.idx} className={`stp-mode-${c.mode} border-b border-token px-2 py-1 whitespace-nowrap`}>
                  {c.mode}
                </td>
              ))}
            </tr>
            <tr>
              <th
                className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left font-medium whitespace-nowrap cursor-pointer hover-bg"
                onClick={() => setSortColumn("name")}
              >
                <span className="inline-flex items-center gap-1">
                  № стенда / Тест
                  {sort.column === "name" && (sort.dir === 1 ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)}
                </span>
              </th>
              {visibleCombos.map((c) => (
                <td key={c.idx} className="surface-2 border-b border-token px-2 py-1 mono font-semibold whitespace-nowrap">
                  {c.standName}
                </td>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((test) => (
              <tr key={test.code} className="hover-bg">
                <th
                  className="sticky left-0 z-10 surface border-r border-token px-2 py-1 text-left font-medium mono whitespace-nowrap cursor-pointer hover-bg"
                  onClick={() => setSortColumn("failcount")}
                  title={test.title}
                >
                  {test.code}
                  {sort.column === "failcount" && (sort.dir === 1 ? <ChevronUp className="w-3 h-3 inline ml-1" /> : <ChevronDown className="w-3 h-3 inline ml-1" />)}
                </th>
                {visibleCombos.map((combo) => {
                  const cell = dataset.cells.get(`${test.code}|${combo.idx}`)!;
                  if (cell.status === "not_run") {
                    return <td key={combo.idx} className="stp-cell-not_run border-b border-token px-2 py-1 text-center">—</td>;
                  }
                  if (statusFilter.size > 0 && !statusFilter.has(cell.status)) {
                    return (
                      <td
                        key={combo.idx}
                        className="stp-cell-not_run border-b border-token px-2 py-1 text-center"
                        title="Скрыто фильтром по статусу"
                      >
                        —
                      </td>
                    );
                  }
                  return (
                    <td
                      key={combo.idx}
                      className={`stp-cell-${cell.status} ${cell.logAvailable ? "" : "stp-cell-log-rotated"} border-b border-token px-2 py-1 cursor-pointer whitespace-nowrap`}
                      onClick={() => setCellTarget({ test, combo, cell })}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span>{STATUS_META[cell.status].label}</span>
                        {cell.logAvailable ? (
                          <button
                            type="button"
                            className="shrink-0 rounded hover:bg-black/10 p-0.5"
                            title="Открыть лог"
                            onClick={(e) => {
                              e.stopPropagation();
                              openLog(test, combo);
                            }}
                          >
                            <ExternalLink className="w-3 h-3" />
                          </button>
                        ) : (
                          <span className="text-[10px] italic shrink-0" title="Слишком старая РЦ, лог ротирован">
                            лог ротирован
                          </span>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {logTarget && <LogViewerModal stand={logTarget.stand} item={logTarget.item} onClose={() => setLogTarget(null)} />}
      {cellTarget && (
        <StpCellActionModal
          version={version}
          target={cellTarget}
          onClose={() => setCellTarget(null)}
          onOpenLog={() => openLog(cellTarget.test, cellTarget.combo)}
        />
      )}
    </div>
  );
}

function StpCellActionModal({
  version,
  target,
  onClose,
  onOpenLog,
}: {
  version: OsVersion;
  target: CellTarget;
  onClose: () => void;
  onOpenLog: () => void;
}) {
  const toast = useToast();
  const { test, combo, cell } = target;
  const reportUrl = `${CONFLUENCE_URL}${encodeURIComponent(version.id)}#${test.code}`;
  const comboLabel = `${combo.standName} / ${combo.mode} / ${combo.kernel}`;

  return (
    <div className="fixed inset-0 z-50 bg-black/55 flex items-center justify-center p-5">
      <div className="surface border border-token rounded w-full max-w-md overflow-hidden shadow-2xl">
        <ModalHeader title={`${test.code} · ${STATUS_META[cell.status].label}`} subtitle={comboLabel} onClose={onClose} />
        <div className="p-4 grid gap-3">
          <div className="text-sm">{test.title}</div>

          <div className="grid gap-2">
            {cell.logAvailable ? (
              <button
                type="button"
                className="btn w-full flex items-center justify-center gap-2"
                onClick={() => {
                  onClose();
                  onOpenLog();
                }}
              >
                <ExternalLink className="w-4 h-4" />
                Лог
              </button>
            ) : (
              <div className="text-xs text-dim italic text-center py-2 surface-2 border border-token rounded">
                Слишком старая РЦ, лог ротирован
              </div>
            )}

            <a
              href={reportUrl}
              target="_blank"
              rel="noreferrer"
              className="btn w-full flex items-center justify-center gap-2"
              onClick={onClose}
            >
              <ExternalLink className="w-4 h-4" />
              Отчёт в Confluence
            </a>

            <button
              type="button"
              className="btn w-full flex items-center justify-center gap-2"
              onClick={() => {
                toast.info(`Тест ${test.code} для ${comboLabel} поставлен в очередь на перезапуск`);
                onClose();
              }}
            >
              <RefreshCcw className="w-4 h-4" />
              Перезапустить
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── таблица 2: тайминг ──────────────────────────────────────────────────────

type TimingScope = "run" | "full";

function StpTimingTable({
  version,
  dataset,
  filters,
  fullCombos,
}: {
  version: OsVersion;
  dataset: StpDataset;
  filters: StpSharedFilters;
  fullCombos: StpCombo[];
}) {
  const [scope, setScope] = useState<TimingScope>("run");

  const combos = scope === "run" ? dataset.combos : fullCombos;
  const allTests = scope === "run" ? dataset.testCases : FULL_TEST_CATALOG;
  const tests = useMemo(
    () => (filters.test.size === 0 ? allTests : allTests.filter((t) => filters.test.has(t.code))),
    [allTests, filters.test],
  );

  const visibleCombos = useMemo(() => combos.filter((c) => matchesCombo(filters, c)), [combos, filters]);

  const lookupSeconds = useCallback(
    (test: StpTestCase, combo: StpCombo): number | null => {
      if (scope === "run") return dataset.cells.get(`${test.code}|${combo.idx}`)?.seconds ?? null;
      const realCombo = dataset.combos.find(
        (c) => c.kernel === combo.kernel && c.mode === combo.mode && c.standName === combo.standName,
      );
      if (!realCombo) return null;
      return dataset.cells.get(`${test.code}|${realCombo.idx}`)?.seconds ?? null;
    },
    [scope, dataset],
  );

  const { emptyCount, totalCount } = useMemo(() => {
    let empty = 0;
    let total = 0;
    for (const test of tests) {
      for (const combo of visibleCombos) {
        total += 1;
        if (lookupSeconds(test, combo) == null) empty += 1;
      }
    }
    return { emptyCount: empty, totalCount: total };
  }, [tests, visibleCombos, lookupSeconds]);

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="text-sm font-medium">Тайминг выполнения · {version.id}</div>
        <div className="surface-2 border border-token rounded p-1 flex items-center gap-1">
          <button type="button" className={`btn btn-sm ${scope === "run" ? "btn-primary" : ""}`} onClick={() => setScope("run")}>
            Только тесты этого РЦ
          </button>
          <button type="button" className={`btn btn-sm ${scope === "full" ? "btn-primary" : ""}`} onClick={() => setScope("full")}>
            Полная таблица
          </button>
        </div>
      </div>

      <div className="px-3 py-1.5 surface-2 border-b border-token text-[11px] text-dim" id="timingNote">
        {scope === "run"
          ? `${visibleCombos.length} комбинаций · ${tests.length} тестов · только реально запущенные тесты этого РЦ`
          : `${visibleCombos.length} комбинаций · ${tests.length} тестов · гипотетическая полная матрица — считается ниже`}
      </div>

      <div className="overflow-auto max-h-[440px]">
        <table className="text-xs border-collapse w-max min-w-full">
          <thead className="sticky top-0 z-20">
            <tr>
              <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                Ядро
              </th>
              {visibleCombos.map((c) => (
                <td key={c.idx} className="surface-2 border-b border-token px-2 py-1 mono text-dim whitespace-nowrap">
                  {c.kernel}
                </td>
              ))}
            </tr>
            <tr>
              <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                Режим
              </th>
              {visibleCombos.map((c) => (
                <td key={c.idx} className={`stp-mode-${c.mode} border-b border-token px-2 py-1 whitespace-nowrap`}>
                  {c.mode}
                </td>
              ))}
            </tr>
            <tr>
              <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                № стенда / Тест
              </th>
              {visibleCombos.map((c) => (
                <td key={c.idx} className="surface-2 border-b border-token px-2 py-1 mono font-semibold whitespace-nowrap">
                  {c.standName}
                </td>
              ))}
            </tr>
          </thead>
          <tbody>
            {tests.map((test) => (
              <tr key={test.code} className="hover-bg">
                <th className="sticky left-0 z-10 surface border-r border-token px-2 py-1 text-left font-medium mono whitespace-nowrap" title={test.title}>
                  {test.code}
                </th>
                {visibleCombos.map((combo) => {
                  const seconds = lookupSeconds(test, combo);
                  if (seconds == null) {
                    return (
                      <td key={combo.idx} className="border-b border-token px-2 py-1 text-dim text-center mono whitespace-nowrap">
                        —
                      </td>
                    );
                  }
                  return (
                    <td key={combo.idx} className="border-b border-token px-2 py-1 mono whitespace-nowrap">
                      {formatDuration(seconds)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {scope === "full" && (
        <div className="px-3 py-2 text-[11px] text-dim border-t border-token">
          гипотетическое покрытие всеми тестами на всех стендах: {emptyCount} из {totalCount} ячеек пустые (не запускалось)
        </div>
      )}
    </div>
  );
}
