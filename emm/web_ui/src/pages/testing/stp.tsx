/**
 * Раздел «СТП» — Состав Тестового Прогона, зеркало Jira Zephyr Scale
 * (`ALLTA MIGRATION.md` §2.5, §6). Источник правды — `testing_service`
 * `/stp/*`: каталог тест-кейсов (`stp_test_cases`), сгенерированные Zephyr
 * test-run'ы (`stp_test_runs`, один на комбинацию РЦ×режим×ядро×стенд) и
 * ячейки матрицы `stp_test_case × stp_test_run` (`stp_cells`) со статусом,
 * который приходит событийно при завершении очереди (§6.2) либо
 * выставляется вручную оператором/QA — ручной override поверх
 * автоматического статуса разрешён планом явно.
 *
 * РЦ (версии ОС) — общий каталог `server_service` (`GET /server/v1/os-versions`),
 * не своя сущность `testing_service`: `stp_test_runs.os_version_id` ссылается
 * на тот же id. Средняя панель (`StpMiddlePanel`) — список этих версий,
 * используется как `middle` в `Shell` по тому же паттерну, что и
 * `pages/server/Server.tsx`. Состояние панели общее с рабочей зоной — обе
 * стороны получают его из `useStpVersionState`, вызываемого один раз в
 * `Testing.tsx`.
 *
 * Zephyr/Confluence — независимые системы (§6.3): emm обновляет статус
 * событийно и создаёт новый тест-кейс сразу в Zephyr одним потоком, но
 * ссылку на страницу конкретного РЦ на Confluence отсюда не строим — base
 * URL живёт per-department в `department_integration_settings`, вне
 * зоны этой волны (см. отчёт).
 */
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Download,
  FilterX,
  ListChecks,
  Loader2,
  Pencil,
  Plus,
  RefreshCcw,
  Search,
  Send,
  Trash2,
} from "lucide-react";
import { naturalCompare } from "@/lib/naturalSort";
import { formatMskShort } from "@/lib/datetime";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listDepartments } from "@/api/auth/departments";
import type { Department } from "@/api/auth/types";
import { listOsVersions } from "@/api/server/osVersions";
import type { OffsetPaginatedResponse, OsVersion } from "@/api/server/types";
import { listNamedTestStands, standName } from "@/api/testing/standCatalogue";
import type { TestStand } from "@/api/testing/types";
import { listTestDefinitions } from "@/api/testing/testDefinitions";
import type { TestDefinition } from "@/api/testing/types";
import {
  addTestToStp,
  createStpTestCase,
  deleteStpTestCase,
  generateStp,
  getStpComposition,
  getStpTestCase,
  getStpTestRun,
  importPullFromLife,
  listStpTestCases,
  listStpTestRunCells,
  listStpTestRuns,
  overrideStpCell,
  previewPullFromLife,
  publishStpMatrix,
  updateStpTestCase,
} from "@/api/testing/stp";
import type {
  StpAddTestOperation,
  StpCell,
  StpCellStatus,
  StpComposition,
  StpCompositionScope,
  StpGeneratePartialError,
  StpGenerateRequest,
  StpMatrixPublishResponse,
  StpPullImportResponse,
  StpPullPreviewResponse,
  StpTestCase,
  StpTestCaseCreateRequest,
  StpTestCaseUpdateRequest,
  StpTestRun,
} from "@/api/testing/types";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

// Режим безопасности Astra — доменная константа (§2.1: `MODE` глобальная
// переменная со `static:["orel","smolensk"]`), не демо-данные.
const STP_MODES = ["orel", "smolensk"] as const;

const CELL_STATUS_META: Record<string, { label: string; badge: "ok" | "danger" | "accent" | "warn" }> = {
  pass: { label: "Пройден", badge: "ok" },
  fail: { label: "Провален", badge: "danger" },
  in_progress: { label: "Выполняется", badge: "accent" },
  not_run: { label: "Не запускался", badge: "warn" },
};

function cellStatusMeta(status: string) {
  return CELL_STATUS_META[status] ?? { label: status, badge: "warn" as const };
}

// Mock-режим (`VITE_USE_MOCK_AUTH=true`) — минимальный набор, чтобы страница
// не была пустой без backend; по образцу `ServicesOsVersions`.
const MOCK_OS_VERSIONS: OsVersion[] = [
  {
    id: "osv_mock_astra187",
    name: "1.8.7.46",
    description: "Astra Linux SE 1.8.7 rc46",
    repositories: [],
    kernels: ["6.12.24-1.el11", "6.12.18-std-def"],
    is_urgent_update: false,
    discovered_at: "2026-09-03T00:00:00Z",
    updated_at: "2026-09-03T00:00:00Z",
  },
];

const MOCK_TEST_CASES: StpTestCase[] = [
  {
    id: "stpcase_mock_1",
    code: "ASTRA-T101",
    title: "Установка с загрузочного носителя",
    zephyr_id: "BT-T101",
    department_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    created_by: "usr_admin",
  },
];

const MOCK_TEST_RUNS: StpTestRun[] = [
  {
    id: "stprun_mock_1",
    os_version_id: "osv_mock_astra187",
    mode: "orel",
    kernel: "6.12.24-1.el11",
    stand_id: "stand_mock_1",
    zephyr_test_run_key: "BT-R1",
    zephyr_folder_path: "/1.8.7.46/orel",
    created_at: "2026-09-03T00:00:00Z",
    updated_at: "2026-09-03T00:00:00Z",
  },
];

const MOCK_CELLS: StpCell[] = [
  {
    id: "stpcell_mock_1",
    stp_test_case_id: "stpcase_mock_1",
    stp_test_run_id: "stprun_mock_1",
    status: "pass",
    is_active: true,
    queue_item_id: "qi_mock_1",
    updated_by: null,
    created_at: "2026-09-03T00:00:00Z",
    updated_at: "2026-09-03T00:00:00Z",
  },
];

const MOCK_COMPOSITION: StpComposition = {
  id: "stpcomp_mock_1",
  department_id: "dep_mock",
  os_version_id: "osv_mock_astra187",
  scope: "changelog",
  revision: 1,
  updated_at: "2026-09-03T00:00:00Z",
  updated_by: "usr_admin",
};

const SCOPE_META: Record<StpCompositionScope, { label: string; badge: "ok" | "accent" }> = {
  full: { label: "Полный набор", badge: "accent" },
  changelog: { label: "По changelog", badge: "ok" },
};

// ── состояние средней панели, общее для StpMiddlePanel и StpWorkzone ───────

export type SortDir = "asc" | "desc";

export interface StpVersionState {
  mockMode: boolean;
  osVersions: OsVersion[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
  search: string;
  setSearch: (v: string) => void;
  sortDir: SortDir;
  toggleSort: () => void;
  filtered: OsVersion[];
  selectedId: string | null;
  setSelectedId: (id: string | null) => void;
  version: OsVersion | null;
}

export function useStpVersionState(): StpVersionState {
  const mockMode = useMockMode();
  const [search, setSearch] = useState("");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const versionsQ = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit: 500 }),
    [],
    { enabled: !mockMode, keepPreviousDataOnError: true },
  );

  const osVersions = mockMode ? MOCK_OS_VERSIONS : (versionsQ.data?.items ?? []);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const list = term ? osVersions.filter((v) => v.name.toLowerCase().includes(term)) : osVersions;
    return [...list].sort((a, b) =>
      sortDir === "asc" ? naturalCompare(a.name, b.name) : naturalCompare(b.name, a.name),
    );
  }, [osVersions, search, sortDir]);

  // Автовыбор первой версии после первой успешной загрузки, чтобы рабочая
  // зона не оставалась пустой без явного клика.
  useEffect(() => {
    if (selectedId === null && filtered.length > 0) setSelectedId(filtered[0].id);
  }, [filtered, selectedId]);

  const version = osVersions.find((v) => v.id === selectedId) ?? null;

  return {
    mockMode,
    osVersions,
    loading: !mockMode && versionsQ.loading,
    error: !mockMode && versionsQ.error ? apiErrMsg(versionsQ.error) : null,
    refetch: versionsQ.refetch,
    search,
    setSearch,
    sortDir,
    toggleSort: () => setSortDir((d) => (d === "asc" ? "desc" : "asc")),
    filtered,
    selectedId,
    setSelectedId,
    version,
  };
}

// ── средняя панель Shell: список версий ОС ──────────────────────────────────

export function StpMiddlePanel({ state }: { state: StpVersionState }) {
  return (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${state.osVersions.length} версиям…`}
            value={state.search}
            onChange={(e) => state.setSearch(e.target.value)}
          />
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
        {state.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center flex items-center justify-center gap-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Загрузка…
          </div>
        )}
        {state.error && (
          <div className="px-3 py-3 text-xs">
            <div className="alert-danger">{state.error}</div>
            <Button size="sm" type="button" className="w-full mt-2" onClick={state.refetch}>
              Повторить
            </Button>
          </div>
        )}
        {!state.loading && !state.error && state.filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">Нет версий по фильтру</div>
        )}
        {!state.loading &&
          !state.error &&
          state.filtered.map((v) => (
            <VersionRow key={v.id} version={v} active={v.id === state.selectedId} onSelect={() => state.setSelectedId(v.id)} />
          ))}
      </div>

      <div className="border-t border-token p-3 shrink-0">
        <Button
          size="sm"
          type="button"
          className="w-full flex items-center justify-center gap-2"
          disabled={state.loading}
          onClick={state.refetch}
        >
          <RefreshCcw className="w-3.5 h-3.5" />
          Обновить список версий
        </Button>
      </div>
    </aside>
  );
}

function VersionRow({ version, active, onSelect }: { version: OsVersion; active: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left pl-4 pr-3 py-2 hover-bg flex items-center gap-2 border-l-2 ${
        active ? "surface-2 border-accent" : "border-transparent"
      }`}
    >
      <span className="font-semibold mono text-sm truncate">{version.name}</span>
      {version.is_urgent_update && <Badge kind="warn" className="shrink-0">UU</Badge>}
      <span className="text-[11px] text-dim ml-auto shrink-0">{formatMskShort(version.discovered_at)}</span>
    </button>
  );
}

// ── рабочая зона: генерация + матрица статусов + каталог тест-кейсов ───────

interface StpFilters {
  mode: Set<string>;
  kernel: Set<string>;
  stand: Set<string>;
  test: Set<string>;
}

function emptyFilters(): StpFilters {
  return { mode: new Set(), kernel: new Set(), stand: new Set(), test: new Set() };
}

export function StpWorkzone({ state }: { state: StpVersionState }) {
  const { version, mockMode } = state;
  const toast = useToast();
  const { persona } = usePersona();

  const [deptFilter, setDeptFilter] = useState<string>(persona.dept_id ?? "");
  const [filters, setFilters] = useState<StpFilters>(emptyFilters());
  const [generateOpen, setGenerateOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [pullOpen, setPullOpen] = useState(false);
  const [caseFormOpen, setCaseFormOpen] = useState<"create" | StpTestCase | null>(null);
  const [cellTarget, setCellTarget] = useState<{ test: StpTestCase; run: StpTestRun; cell: StpCell } | null>(null);
  const [runTarget, setRunTarget] = useState<StpTestRun | null>(null);

  // Смена РЦ в средней панели — старый выбор фильтров может не иметь смысла
  // для новой версии (другие ядра/стенды), поэтому сбрасываем.
  useEffect(() => {
    setFilters(emptyFilters());
  }, [version?.id]);

  const departmentsQ = useQuery<Department[]>(() => listDepartments(), [], { enabled: !mockMode });
  const departments = mockMode ? [] : (departmentsQ.data ?? []);

  const standsQ = useQuery(
    () => listNamedTestStands({ department_id: deptFilter || undefined }),
    [deptFilter],
    { enabled: !mockMode },
  );
  const stands: TestStand[] = mockMode ? [] : (standsQ.data ?? []);
  const standIds = useMemo(() => new Set(stands.map((s) => s.id)), [stands]);

  const testCasesQ = useQuery(
    () => listStpTestCases({ department_id: deptFilter || undefined, limit: 500 }),
    [deptFilter],
    { enabled: !mockMode },
  );
  const testCases: StpTestCase[] = mockMode ? MOCK_TEST_CASES : (testCasesQ.data?.items ?? []);

  const testRunsQ = useQuery(
    () => listStpTestRuns({ os_version_id: version?.id, limit: 500 }),
    [version?.id],
    { enabled: !mockMode && !!version },
  );
  const runsForVersion: StpTestRun[] = mockMode
    ? MOCK_TEST_RUNS
    : version
      ? (testRunsQ.data?.items ?? [])
      : [];

  // Текущий активный состав СТП (scope+revision, §D4/D5) для выбранной пары
  // (отдел, РЦ) — не своя сущность на стенд, одна строка на (department, os_version_id).
  const compositionQ = useQuery(
    () => getStpComposition({ os_version_id: version!.id, department_id: deptFilter || undefined }),
    [version?.id, deptFilter],
    { enabled: !mockMode && !!version },
  );
  const composition: StpComposition | null = mockMode
    ? MOCK_COMPOSITION
    : version
      ? (compositionQ.data ?? null)
      : null;

  // Фильтр по отделу режется через принадлежность стенда отделу — у самого
  // прогона `department_id` нет (только `stand_id`), см. `StpTestRun` схему.
  const visibleRuns = useMemo(() => {
    return runsForVersion.filter((r) => {
      if (deptFilter && stands.length > 0 && !standIds.has(r.stand_id)) return false;
      if (filters.mode.size > 0 && !filters.mode.has(r.mode)) return false;
      if (filters.kernel.size > 0 && !filters.kernel.has(r.kernel)) return false;
      if (filters.stand.size > 0 && !filters.stand.has(r.stand_id)) return false;
      return true;
    });
  }, [runsForVersion, deptFilter, stands, standIds, filters]);

  const visibleTestCases = useMemo(
    () => (filters.test.size === 0 ? testCases : testCases.filter((t) => filters.test.has(t.code))),
    [testCases, filters.test],
  );

  // ── ячейки матрицы: одна выборка на каждый видимый прогон ────────────────
  const [cellsByRun, setCellsByRun] = useState<Record<string, StpCell[]>>({});
  const [cellsLoading, setCellsLoading] = useState(false);
  const [cellsError, setCellsError] = useState<string | null>(null);
  const runIdsKey = visibleRuns.map((r) => r.id).sort().join(",");

  async function loadCells(runIds: string[]) {
    if (mockMode) {
      setCellsByRun({ [MOCK_TEST_RUNS[0].id]: MOCK_CELLS });
      return;
    }
    if (runIds.length === 0) {
      setCellsByRun({});
      return;
    }
    setCellsLoading(true);
    setCellsError(null);
    try {
      const pairs = await Promise.all(runIds.map(async (id) => [id, await listStpTestRunCells(id)] as const));
      setCellsByRun(Object.fromEntries(pairs));
    } catch (e) {
      setCellsError(apiErrMsg(e));
    } finally {
      setCellsLoading(false);
    }
  }

  useEffect(() => {
    loadCells(runIdsKey ? runIdsKey.split(",") : []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runIdsKey, mockMode]);

  async function refetchRunCells(runId: string) {
    if (mockMode) return;
    try {
      const cells = await listStpTestRunCells(runId);
      setCellsByRun((current) => ({ ...current, [runId]: cells }));
    } catch (e) {
      toast.error(apiErrMsg(e));
    }
  }

  function findCell(testCaseId: string, runId: string): StpCell | undefined {
    return cellsByRun[runId]?.find((c) => c.stp_test_case_id === testCaseId);
  }

  const modeOptions: DropdownOption[] = STP_MODES.map((m) => ({ value: m, label: m }));
  const kernelOptions: DropdownOption[] = useMemo(() => {
    const kernels = new Set(runsForVersion.map((r) => r.kernel));
    return Array.from(kernels).map((k) => ({ value: k, label: k }));
  }, [runsForVersion]);
  const standOptions: DropdownOption[] = useMemo(
    () => Array.from(new Set(runsForVersion.map((r) => r.stand_id))).map((id) => ({ value: id, label: standName(stands.find((stand) => stand.id === id)) })),
    [runsForVersion, stands],
  );
  const testOptions: DropdownOption[] = testCases.map((t) => ({ value: t.code, label: `${t.title} · ${t.code}` }));
  const departmentOptions: DropdownOption[] = departments.map((d) => ({ value: d.id, label: d.name }));

  const hasFilters = filters.mode.size > 0 || filters.kernel.size > 0 || filters.stand.size > 0 || filters.test.size > 0;

  return (
    <div className="grid gap-4 min-w-0">
      <div className="alert-warn text-xs">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
        <span>
          СТП — зеркало Jira Zephyr Scale. Статус ячейки обновляется автоматически при завершении задачи в очереди,
          но оператор/QA может выставить его вручную (override) — это не откатывается автоматикой.
        </span>
      </div>

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg font-semibold mono">{version ? version.name : "Версия не выбрана"}</span>
            {version?.is_urgent_update && <Badge kind="warn">хотфикс</Badge>}
          </div>
          {version && (
            <div className="text-xs text-dim mt-1">
              обнаружена {formatMskShort(version.discovered_at)} · ядра: {version.kernels.join(", ") || "—"}
            </div>
          )}
          {version && (
            <div className="text-xs mt-1 flex items-center gap-1.5">
              <span className="text-dim">Состав СТП:</span>
              {composition?.scope ? (
                <>
                  <Badge kind={SCOPE_META[composition.scope].badge}>{SCOPE_META[composition.scope].label}</Badge>
                  <span className="text-dim">ревизия {composition.revision}</span>
                </>
              ) : (
                <span className="text-dim italic">ещё не сгенерирован</span>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <label className="flex items-center gap-1.5 text-xs text-dim">
            <span>Отдел:</span>
            <Dropdown
              mode="single"
              options={departmentOptions}
              value={deptFilter}
              onChange={setDeptFilter}
              placeholder="Все"
              searchable
            />
          </label>
          <Button
            type="button"
            variant="primary"
            className="inline-flex items-center gap-2"
            disabled={!version}
            onClick={() => setGenerateOpen(true)}
          >
            <Plus className="w-3.5 h-3.5" />
            Состав СТП
          </Button>
          <Button
            type="button"
            className="inline-flex items-center gap-2"
            disabled={!version}
            onClick={() => setPublishOpen(true)}
          >
            <Send className="w-3.5 h-3.5" />
            Опубликовать в Confluence
          </Button>
          <Button
            type="button"
            className="inline-flex items-center gap-2"
            disabled={!version}
            onClick={() => setPullOpen(true)}
          >
            <Download className="w-3.5 h-3.5" />
            Pull СТП из life
          </Button>
        </div>
      </div>

      <div className="surface border border-token rounded p-3 flex items-center gap-2 flex-wrap">
        <span className="text-xs text-dim font-medium">Фильтры:</span>
        <Dropdown mode="multi" label="Режим" options={modeOptions} value={filters.mode} onChange={(v) => setFilters({ ...filters, mode: v })} />
        <Dropdown mode="multi" label="Ядро" searchable options={kernelOptions} value={filters.kernel} onChange={(v) => setFilters({ ...filters, kernel: v })} />
        <Dropdown mode="multi" label="Стенд" searchable options={standOptions} value={filters.stand} onChange={(v) => setFilters({ ...filters, stand: v })} />
        <Dropdown mode="multi" label="Тест" searchable options={testOptions} value={filters.test} onChange={(v) => setFilters({ ...filters, test: v })} />
        <Button
          size="sm"
          type="button"
          className="ml-auto inline-flex items-center gap-1.5"
          disabled={!hasFilters}
          onClick={() => setFilters(emptyFilters())}
        >
          <FilterX className="w-3.5 h-3.5" />
          Сбросить фильтры
        </Button>
      </div>

      <StpMatrix
        testCases={visibleTestCases}
        runs={visibleRuns}
        findCell={findCell}
        loading={!mockMode && (testCasesQ.loading || (!!version && testRunsQ.loading) || cellsLoading)}
        error={
          (!mockMode && testCasesQ.error && apiErrMsg(testCasesQ.error)) ||
          (!mockMode && testRunsQ.error && apiErrMsg(testRunsQ.error)) ||
          cellsError ||
          null
        }
        onOpenCell={(test, run, cell) => setCellTarget({ test, run, cell })}
        onOpenRun={(run) => setRunTarget(run)}
      />

      <StpTestCaseCatalog
        mockMode={mockMode}
        testCases={testCases}
        loading={!mockMode && testCasesQ.loading}
        error={!mockMode && testCasesQ.error ? apiErrMsg(testCasesQ.error) : null}
        onRetry={testCasesQ.refetch}
        onCreate={() => setCaseFormOpen("create")}
        onEdit={(tc) => setCaseFormOpen(tc)}
        onChanged={testCasesQ.refetch}
      />

      {generateOpen && version && (
        <StpGenerateModal
          mockMode={mockMode}
          version={version}
          departmentId={deptFilter || undefined}
          composition={composition}
          onClose={() => setGenerateOpen(false)}
          onDone={() => {
            testRunsQ.refetch();
            compositionQ.refetch();
          }}
        />
      )}

      {publishOpen && version && (
        <StpMatrixPublishModal
          mockMode={mockMode}
          version={version}
          departmentId={deptFilter || undefined}
          onClose={() => setPublishOpen(false)}
        />
      )}

      {pullOpen && version && (
        <StpPullFromLifeModal
          mockMode={mockMode}
          version={version}
          departmentId={deptFilter || undefined}
          onClose={() => setPullOpen(false)}
          onImported={() => {
            testRunsQ.refetch();
            compositionQ.refetch();
          }}
        />
      )}

      {caseFormOpen && (
        <StpTestCaseFormModal
          mockMode={mockMode}
          existing={caseFormOpen === "create" ? null : caseFormOpen}
          defaultDepartmentId={deptFilter}
          departmentOptions={departmentOptions}
          onClose={() => setCaseFormOpen(null)}
          onDone={() => {
            setCaseFormOpen(null);
            testCasesQ.refetch();
          }}
        />
      )}

      {cellTarget && (
        <StpCellModal
          mockMode={mockMode}
          target={cellTarget}
          onClose={() => setCellTarget(null)}
          onOverridden={() => {
            refetchRunCells(cellTarget.run.id);
          }}
        />
      )}

      {runTarget && (
        <StpRunDetailModal
          mockMode={mockMode}
          run={runTarget}
          testCases={testCases}
          cells={cellsByRun[runTarget.id] ?? []}
          departmentId={deptFilter || undefined}
          onClose={() => setRunTarget(null)}
          onAdded={() => refetchRunCells(runTarget.id)}
        />
      )}
    </div>
  );
}

// ── таблица: матрица статусов тест-кейс × прогон ────────────────────────────

function StpMatrix({
  testCases,
  runs,
  findCell,
  loading,
  error,
  onOpenCell,
  onOpenRun,
}: {
  testCases: StpTestCase[];
  runs: StpTestRun[];
  findCell: (testCaseId: string, runId: string) => StpCell | undefined;
  loading: boolean;
  error: string | null;
  onOpenCell: (test: StpTestCase, run: StpTestRun, cell: StpCell) => void;
  onOpenRun: (run: StpTestRun) => void;
}) {
  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="text-sm font-medium flex items-center gap-2">
          <ListChecks className="w-4 h-4 text-accent" />
          Матрица статусов
        </div>
        {loading && (
          <span className="text-xs text-dim inline-flex items-center gap-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> загрузка…
          </span>
        )}
      </div>

      {error && <div className="alert-danger m-3 text-xs">{error}</div>}

      <div className="px-3 py-1.5 surface-2 border-b border-token text-[11px] text-dim">
        {runs.length} прогонов · {testCases.length} тест-кейсов
      </div>

      {runs.length === 0 || testCases.length === 0 ? (
        <div className="p-8 text-center text-dim text-sm">
          {runs.length === 0
            ? "Для этой РЦ ещё нет прогонов — сгенерируйте СТП кнопкой выше."
            : "Нет тест-кейсов по выбранному отделу/фильтру."}
        </div>
      ) : (
        <div className="overflow-auto max-h-[440px]">
          <table className="text-xs border-collapse w-max min-w-full">
            <thead className="sticky top-0 z-20">
              <tr>
                <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                  Ядро
                </th>
                {runs.map((r) => (
                  <td key={r.id} className="surface-2 border-b border-token px-2 py-1 mono text-dim whitespace-nowrap">
                    {r.kernel}
                  </td>
                ))}
              </tr>
              <tr>
                <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left text-dim font-medium whitespace-nowrap">
                  Режим
                </th>
                {runs.map((r) => (
                  <td key={r.id} className={`stp-mode-${r.mode} border-b border-token px-2 py-1 whitespace-nowrap`}>
                    {r.mode}
                  </td>
                ))}
              </tr>
              <tr>
                <th className="sticky left-0 z-30 surface-2 border-r border-b border-token px-2 py-1 text-left font-medium whitespace-nowrap">
                  Стенд / Прогон
                </th>
                {runs.map((r) => (
                  <td key={r.id} className="surface-2 border-b border-token px-2 py-1">
                    <button
                      type="button"
                      className="mono font-semibold whitespace-nowrap hover:underline"
                      title="Карточка прогона"
                      onClick={() => onOpenRun(r)}
                    >
                      {r.stand_id}
                    </button>
                  </td>
                ))}
              </tr>
            </thead>
            <tbody>
              {testCases.map((test) => (
                <tr key={test.id} className="hover-bg">
                  <th className="sticky left-0 z-10 surface border-r border-token px-2 py-1 text-left font-medium mono whitespace-nowrap" title={test.title}>
                    {test.code}
                  </th>
                  {runs.map((run) => {
                    const cell = findCell(test.id, run.id);
                    if (!cell) {
                      return (
                        <td key={run.id} className="stp-cell-not_run border-b border-token px-2 py-1 text-center" title="Ячейка ещё не создана">
                          —
                        </td>
                      );
                    }
                    const meta = cellStatusMeta(cell.status);
                    return (
                      <td
                        key={run.id}
                        className={`stp-cell-${cell.status} border-b border-token px-2 py-1 cursor-pointer whitespace-nowrap ${
                          cell.is_active ? "" : "opacity-50"
                        }`}
                        onClick={() => onOpenCell(test, run, cell)}
                        title={cell.is_active ? undefined : "Исключён из текущего активного состава — история сохранена"}
                      >
                        <Badge kind={meta.badge}>{meta.label}</Badge>
                        {cell.updated_by && (
                          <span className="ml-1 text-[10px] italic" title="Ручной override">
                            override
                          </span>
                        )}
                        {!cell.is_active && (
                          <span className="ml-1 text-[10px] italic" title="Исключён из активного состава">
                            искл.
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── модалка: ячейка матрицы + ручной override ───────────────────────────────

const CELL_STATUS_OPTIONS: StpCellStatus[] = ["not_run", "in_progress", "pass", "fail"];

function StpCellModal({
  mockMode,
  target,
  onClose,
  onOverridden,
}: {
  mockMode: boolean;
  target: { test: StpTestCase; run: StpTestRun; cell: StpCell };
  onClose: () => void;
  onOverridden: () => void;
}) {
  const toast = useToast();
  const { test, run, cell } = target;
  const [status, setStatus] = useState<string>(cell.status);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (mockMode) {
      toast.warn("Mock-режим — override не отправляется на backend.");
      onClose();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await overrideStpCell(cell.id, { status });
      toast.success(`Статус ${test.code} переопределён на «${cellStatusMeta(status).label}»`);
      onOverridden();
      onClose();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`${test.code} · ${cellStatusMeta(cell.status).label}`}
      subtitle={`${run.mode} / ${run.kernel} / ${run.stand_id}`}
    >
      <div className="grid gap-3">
        <div className="text-sm">{test.title}</div>
        <div className="text-xs text-dim grid gap-1">
          <div>queue_item_id: <span className="mono">{cell.queue_item_id ?? "—"}</span></div>
          <div>
            в активном составе: <span className="mono">{cell.is_active ? "да" : "нет — исключён, история сохранена"}</span>
          </div>
          <div>обновлено: <span className="mono">{formatMskShort(cell.updated_at)}</span>{cell.updated_by ? ` · вручную (${cell.updated_by})` : " · автоматически"}</div>
        </div>

        <label className="grid gap-1 text-xs text-dim">
          <span>Ручной override статуса</span>
          <Dropdown
            mode="single"
            options={CELL_STATUS_OPTIONS.map((s) => ({ value: s, label: cellStatusMeta(s).label }))}
            value={status}
            onChange={setStatus}
          />
        </label>

        {err && <div className="alert-danger text-xs">{err}</div>}

        <div className="flex gap-2 justify-end">
          <Button type="button" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button type="button" variant="primary" onClick={submit} disabled={busy || status === cell.status}>
            {busy ? "..." : "Сохранить override"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ── модалка: карточка прогона (Zephyr) ──────────────────────────────────────

function StpRunDetailModal({
  mockMode,
  run,
  testCases,
  cells,
  departmentId,
  onClose,
  onAdded,
}: {
  mockMode: boolean;
  run: StpTestRun;
  testCases: StpTestCase[];
  cells: StpCell[];
  departmentId?: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [detail, setDetail] = useState<StpTestRun>(run);
  const [loading, setLoading] = useState(!mockMode);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (mockMode) return;
    let cancelled = false;
    setLoading(true);
    getStpTestRun(run.id)
      .then((r) => {
        if (!cancelled) setDetail(r);
      })
      .catch((e) => {
        if (!cancelled) setErr(apiErrMsg(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mockMode, run.id]);

  // Тесты каталога EMM, закреплённые за стендом этого прогона, у которых
  // ещё нет ячейки здесь — либо тест-кейса СТП вовсе нет, либо он есть, но
  // не связан ячейкой с ИМЕННО этим прогоном (§D6: ручное добавление одного
  // теста, не задевая остальной состав).
  const testDefsQ = useQuery(
    () => listTestDefinitions({ department_id: departmentId, limit: 500 }),
    [departmentId],
    { enabled: !mockMode },
  );
  const missingTests = useMemo(() => {
    if (mockMode) return [];
    const caseIdByCode = new Map(testCases.map((tc) => [tc.code, tc.id]));
    const caseIdsWithCell = new Set(cells.map((c) => c.stp_test_case_id));
    return (testDefsQ.data?.items ?? []).filter((t) => {
      if (t.pinned_stand_id !== run.stand_id) return false;
      const caseId = caseIdByCode.get(t.code);
      return !caseId || !caseIdsWithCell.has(caseId);
    });
  }, [mockMode, testDefsQ.data, testCases, cells, run.stand_id]);

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Карточка СТП-прогона"
      subtitle={`${detail.mode} / ${detail.kernel} / ${detail.stand_id}`}
    >
      <div className="grid gap-2 text-xs">
        {loading && (
          <div className="text-dim inline-flex items-center gap-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> загрузка…
          </div>
        )}
        {err && <div className="alert-danger">{err}</div>}
        <div>id: <span className="mono">{detail.id}</span></div>
        <div>os_version_id: <span className="mono">{detail.os_version_id}</span></div>
        <div>zephyr_test_run_key: <span className="mono">{detail.zephyr_test_run_key ?? "— (генерация ещё не дошла до Zephyr)"}</span></div>
        <div>zephyr_folder_path: <span className="mono">{detail.zephyr_folder_path ?? "—"}</span></div>
        <div>создан: <span className="mono">{formatMskShort(detail.created_at)}</span></div>
        <div>обновлён: <span className="mono">{formatMskShort(detail.updated_at)}</span></div>
      </div>

      <div className="border-t border-token mt-3 pt-3 grid gap-2">
        <div className="text-xs font-medium text-dim">Тесты стенда без ячейки в этом прогоне</div>
        {mockMode && <div className="text-xs text-dim italic">Mock-режим — недоступно.</div>}
        {!mockMode && testDefsQ.loading && (
          <div className="text-xs text-dim inline-flex items-center gap-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> загрузка…
          </div>
        )}
        {!mockMode && testDefsQ.error && <div className="alert-danger text-xs">{apiErrMsg(testDefsQ.error)}</div>}
        {!mockMode && !testDefsQ.loading && !testDefsQ.error && missingTests.length === 0 && (
          <div className="text-xs text-dim italic">Все привязанные к стенду тесты уже в составе.</div>
        )}
        {!mockMode &&
          missingTests.map((t) => <StpAddTestRow key={t.id} test={t} run={run} onAdded={onAdded} />)}
      </div>

      <div className="flex justify-end mt-3">
        <Button type="button" onClick={onClose}>Закрыть</Button>
      </div>
    </Modal>
  );
}

// ── ручное добавление одного теста EMM в прогон (§D6/D7) ────────────────────

const ADD_TEST_STEP_LABELS: [keyof StpAddTestOperation, string][] = [
  ["zephyr_testcase_created", "testcase"],
  ["zephyr_added_to_run", "в ран"],
  ["stp_cell_created", "ячейка"],
  ["life_published", "life"],
];

function StpAddTestRow({ test, run, onAdded }: { test: TestDefinition; run: StpTestRun; onAdded: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [op, setOp] = useState<StpAddTestOperation | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const result = await addTestToStp(run.id, { test_id: test.id });
      setOp(result);
      if (result.status === "succeeded") {
        toast.success(`${test.code} добавлен в СТП`);
        onAdded();
      } else {
        toast.warn(`${test.code}: ${result.status}${result.last_error ? " — " + result.last_error : ""}`);
      }
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center justify-between gap-2 text-xs surface-2 border border-token rounded px-2 py-1.5">
      <div className="min-w-0">
        <div className="mono font-medium truncate">{test.code}</div>
        <div className="text-dim truncate">{test.full_name}</div>
        {op && (
          <div className="mt-1 flex items-center gap-1 flex-wrap">
            {ADD_TEST_STEP_LABELS.map(([key, label]) => (
              <Badge key={key} kind={op[key] ? "ok" : "warn"}>{label}</Badge>
            ))}
          </div>
        )}
        {err && <div className="text-danger mt-1">{err}</div>}
      </div>
      <Button size="sm" type="button" variant="primary" disabled={busy} onClick={submit}>
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Добавить в СТП"}
      </Button>
    </div>
  );
}

// ── состав СТП: переключатель «Полный набор»/«По changelog» (§D4/D5) ───────

function StpGenerateModal({
  mockMode,
  version,
  departmentId,
  composition,
  onClose,
  onDone,
}: {
  mockMode: boolean;
  version: OsVersion;
  departmentId?: string;
  composition: StpComposition | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<StpCompositionScope | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<{ scope: StpCompositionScope; touchedCount: number; errors: StpGeneratePartialError[] } | null>(null);

  const currentScope = composition?.scope ?? null;

  async function submit(scope: StpCompositionScope) {
    if (mockMode) {
      toast.warn("Mock-режим — переключение состава не отправляется на backend.");
      onClose();
      return;
    }
    setBusy(scope);
    setErr(null);
    try {
      const body: StpGenerateRequest = {
        os_version_id: version.id,
        scope,
        department_id: departmentId,
      };
      const res = await generateStp(body);
      setResult({ scope, touchedCount: res.test_runs.length, errors: res.errors });
      if (res.errors.length === 0) {
        toast.success(`${SCOPE_META[scope].label}: прогонов в составе — ${res.test_runs.length}`);
      } else {
        toast.warn(`${SCOPE_META[scope].label}: ${res.test_runs.length} прогонов, ${res.errors.length} стендов с ошибкой`);
      }
      onDone();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Состав СТП"
      subtitle={version.name}
    >
      <div className="grid gap-3">
        {!result && (
          <>
            <p className="text-sm">
              «Полный набор» добавляет в состав все закреплённые за стендами тесты отдела. «По changelog» оставляет
              только тесты, затронутые изменившимися компонентами этой РЦ. Повтор с тем же вариантом ничего не
              дублирует; переключение между вариантами не теряет уже полученные результаты — исключённые тесты
              просто скрываются из активного состава и возвращаются при обратном переключении.
            </p>
            <div className="text-xs text-dim">
              Текущий состав:{" "}
              {currentScope ? (
                <>
                  <Badge kind={SCOPE_META[currentScope].badge}>{SCOPE_META[currentScope].label}</Badge>
                  {" "}(ревизия {composition?.revision})
                </>
              ) : (
                <span className="italic">ещё не сгенерирован</span>
              )}
            </div>
            {err && <div className="alert-danger text-xs">{err}</div>}
            <div className="flex gap-2 justify-end flex-wrap">
              <Button type="button" onClick={onClose} disabled={busy !== null}>
                Отмена
              </Button>
              <Button type="button" onClick={() => submit("changelog")} disabled={busy !== null}>
                {busy === "changelog" ? "..." : "По changelog"}
              </Button>
              <Button type="button" variant="primary" onClick={() => submit("full")} disabled={busy !== null}>
                {busy === "full" ? "..." : "Полный набор"}
              </Button>
            </div>
          </>
        )}

        {result && (
          <>
            <div className="text-sm flex items-center gap-1.5">
              <span>Состав:</span>
              <Badge kind={SCOPE_META[result.scope].badge}>{SCOPE_META[result.scope].label}</Badge>
              <span>прогонов в составе: <span className="font-semibold">{result.touchedCount}</span></span>
            </div>
            {result.errors.length > 0 && (
              <div className="alert-warn text-xs grid gap-2">
                <div className="flex items-center gap-1.5 font-medium">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  Часть стендов провалилась ({result.errors.length}) — остальные прогоны затронуты успешно:
                </div>
                <ul className="grid gap-1">
                  {result.errors.map((e, i) => (
                    <li key={`${e.stand_id}-${i}`} className="mono text-[11px]">
                      {e.stand_id} · {e.error_code}: {e.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="flex justify-end">
              <Button type="button" variant="primary" onClick={onClose}>
                Готово
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// ── публикация сводной СТП-матрицы в Confluence ─────────────────────────────

const MATRIX_STATUS_LABELS: Record<string, { label: string; className: string }> = {
  posted: { label: "Опубликовано", className: "text-ok" },
  skipped_not_configured: {
    label: "Не настроено — заполните Confluence-пространство/родительскую страницу СТП-матрицы в интеграциях отдела",
    className: "text-warn",
  },
  skipped_no_test_runs: { label: "Для этого РЦ у отдела ещё нет СТП-прогонов", className: "text-warn" },
  failed: { label: "Ошибка публикации", className: "text-danger" },
};

function StpMatrixPublishModal({
  mockMode,
  version,
  departmentId,
  onClose,
}: {
  mockMode: boolean;
  version: OsVersion;
  departmentId?: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<StpMatrixPublishResponse | null>(null);

  async function submit() {
    if (mockMode) {
      toast.warn("Mock-режим — публикация не отправляется на backend.");
      onClose();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await publishStpMatrix({ os_version_id: version.id, department_id: departmentId });
      setResult(res);
      if (res.status === "posted") {
        toast.success("СТП-матрица опубликована в Confluence");
      } else {
        toast.warn(MATRIX_STATUS_LABELS[res.status]?.label ?? res.status);
      }
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Опубликовать СТП-матрицу"
      subtitle={version.name}
    >
      <div className="grid gap-3">
        {!result && (
          <>
            <p className="text-sm">
              Сводная таблица статусов всех прогонов этого РЦ (все режимы/ядра/стенды отдела) будет
              опубликована страницей в Confluence — пространство и корневая страница иерархии берутся из
              настроек интеграции отдела.
            </p>
            {err && <div className="alert-danger text-xs">{err}</div>}
            <div className="flex gap-2 justify-end">
              <Button type="button" onClick={onClose} disabled={busy}>
                Отмена
              </Button>
              <Button type="button" variant="primary" onClick={submit} disabled={busy}>
                {busy ? "..." : "Опубликовать"}
              </Button>
            </div>
          </>
        )}

        {result && (
          <>
            <div className={`text-sm ${MATRIX_STATUS_LABELS[result.status]?.className ?? "text-dim"}`}>
              {MATRIX_STATUS_LABELS[result.status]?.label ?? result.status}
            </div>
            {result.error && <div className="text-xs text-dim">{result.error}</div>}
            <div className="flex justify-end">
              <Button type="button" variant="primary" onClick={onClose}>
                Готово
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// ── pull СТП из life (§D8) ───────────────────────────────────────────────────

function StpPullFromLifeModal({
  mockMode,
  version,
  departmentId,
  onClose,
  onImported,
}: {
  mockMode: boolean;
  version: OsVersion;
  departmentId?: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const toast = useToast();
  const [loading, setLoading] = useState(!mockMode);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<StpPullPreviewResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<StpPullImportResponse | null>(null);

  useEffect(() => {
    if (mockMode) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    previewPullFromLife({ os_version_id: version.id, department_id: departmentId })
      .then((res) => {
        if (cancelled) return;
        setPreview(res);
        setSelected(new Set(res.items.filter((i) => !i.needs_manual_mapping).map((i) => i.zephyr_key)));
      })
      .catch((e) => {
        if (!cancelled) setErr(apiErrMsg(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mockMode, version.id, departmentId]);

  function toggle(key: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function submit() {
    if (mockMode) {
      toast.warn("Mock-режим — импорт не отправляется на backend.");
      onClose();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await importPullFromLife({
        os_version_id: version.id, department_id: departmentId, zephyr_keys: Array.from(selected),
      });
      setResult(res);
      onImported();
      if (res.conflicts_count > 0 || res.failed_count > 0) {
        toast.warn(`Импорт завершён с расхождениями: конфликтов ${res.conflicts_count}, ошибок ${res.failed_count}`);
      } else {
        toast.success(`Импортировано: заведено ${res.created_runs}, сверено ${res.matched_runs} прогонов`);
      }
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Pull СТП из life"
      subtitle={version.name}
    >
      <div className="grid gap-3">
        {!result && (
          <p className="text-sm text-dim">
            Читает test-run&apos;ы, уже существующие в Zephyr для этой РЦ (свои, легаси или заведённые вручную),
            и позволяет затянуть их в EMM. Только чтение из life — предпросмотр ничего не пишет, импорт не
            публикует ничего обратно и не перезаписывает молча локально изменённые ячейки.
          </p>
        )}

        {loading && (
          <div className="flex items-center gap-2 text-sm text-dim">
            <Loader2 className="w-4 h-4 animate-spin" />
            Ищем test-run&apos;ы в Zephyr…
          </div>
        )}

        {err && <div className="alert-danger text-xs">{err}</div>}

        {!loading && preview && !result && (
          <>
            <div className="text-xs text-dim">
              Найдено {preview.total_found} · новых {preview.new_count} · уже импортировано{" "}
              {preview.already_imported_count} · нужна ручная сверка {preview.needs_manual_mapping_count}
            </div>
            {preview.items.length === 0 ? (
              <div className="text-sm text-dim italic">В папке {preview.folder} ничего не найдено.</div>
            ) : (
              <div className="border border-token rounded overflow-auto max-h-80">
                <table className="w-full text-xs">
                  <thead className="surface-2 sticky top-0">
                    <tr>
                      <th className="p-2 text-left w-8"></th>
                      <th className="p-2 text-left">Zephyr</th>
                      <th className="p-2 text-left">Контекст</th>
                      <th className="p-2 text-left">Состав</th>
                      <th className="p-2 text-left">Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.items.map((item) => (
                      <tr key={item.zephyr_key} className="border-t border-token">
                        <td className="p-2">
                          <input
                            type="checkbox"
                            disabled={item.needs_manual_mapping}
                            checked={selected.has(item.zephyr_key)}
                            onChange={() => toggle(item.zephyr_key)}
                          />
                        </td>
                        <td className="p-2 mono">
                          {item.zephyr_link ? (
                            <a href={item.zephyr_link} target="_blank" rel="noreferrer" className="link">
                              {item.zephyr_key}
                            </a>
                          ) : (
                            item.zephyr_key
                          )}
                          <div className="text-dim">{item.name}</div>
                        </td>
                        <td className="p-2">
                          {item.parsed_mode ? (
                            <span>
                              {item.parsed_mode} · {item.parsed_kernel} ·{" "}
                              {item.stand_id ?? item.parsed_stand_token ?? "—"}
                            </span>
                          ) : (
                            <span className="text-dim italic">не распознано</span>
                          )}
                        </td>
                        <td className="p-2">
                          {item.composition.case_count} кейсов, из них новых {item.composition.new_case_count}
                        </td>
                        <td className="p-2">
                          {item.needs_manual_mapping ? (
                            <Badge kind="warn">ручная сверка: {item.mapping_issue}</Badge>
                          ) : item.already_imported ? (
                            <Badge kind="accent">уже импортирован</Badge>
                          ) : (
                            <Badge kind="ok">новый</Badge>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <Button type="button" onClick={onClose} disabled={busy}>
                Отмена
              </Button>
              <Button
                type="button"
                variant="primary"
                onClick={submit}
                disabled={busy || selected.size === 0}
                className="inline-flex items-center gap-2"
              >
                {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Импортировать выбранные ({selected.size})
              </Button>
            </div>
          </>
        )}

        {result && (
          <>
            <div className="text-sm">
              Заведено прогонов: {result.created_runs} · сверено: {result.matched_runs} · тест-кейсов заведено:{" "}
              {result.cases_created} · ячеек заведено: {result.cells_created}
            </div>
            {result.conflicts_count > 0 && (
              <div className="alert-warn text-xs">
                {result.conflicts_count} ячеек с расходящимся локальным статусом НЕ перезаписаны — сверьте вручную.
              </div>
            )}
            {result.failed_count > 0 && (
              <div className="alert-danger text-xs">{result.failed_count} прогонов не удалось прочитать из Zephyr.</div>
            )}
            {result.skipped_count > 0 && (
              <div className="alert-warn text-xs">{result.skipped_count} прогонов пропущены — нужна ручная сверка.</div>
            )}
            {result.results.some((r) => r.error) && (
              <ul className="text-xs text-dim list-disc pl-4">
                {result.results.filter((r) => r.error).map((r) => (
                  <li key={r.zephyr_key}>{r.zephyr_key}: {r.error}</li>
                ))}
              </ul>
            )}
            <div className="flex justify-end">
              <Button type="button" variant="primary" onClick={onClose}>
                Готово
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// ── каталог тест-кейсов СТП: CRUD ────────────────────────────────────────────

function StpTestCaseCatalog({
  mockMode,
  testCases,
  loading,
  error,
  onRetry,
  onCreate,
  onEdit,
  onChanged,
}: {
  mockMode: boolean;
  testCases: StpTestCase[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onCreate: () => void;
  onEdit: (tc: StpTestCase) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [busyId, setBusyId] = useState<string | null>(null);

  async function onDelete(tc: StpTestCase) {
    if (mockMode) {
      toast.warn("Mock-режим — удаление не отправляется на backend.");
      return;
    }
    if (!(await confirm.confirm({ message: `Удалить тест-кейс «${tc.code}»?`, danger: true, confirmLabel: "Удалить" }))) return;
    setBusyId(tc.id);
    try {
      await deleteStpTestCase(tc.id);
      toast.success("Тест-кейс удалён");
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="text-sm font-medium">Каталог тест-кейсов СТП</div>
        <Button type="button" size="sm" className="inline-flex items-center gap-1.5" onClick={onCreate}>
          <Plus className="w-3.5 h-3.5" />
          Добавить тест-кейс
        </Button>
      </div>

      {loading && (
        <div className="p-4 text-xs text-dim inline-flex items-center gap-1.5">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> загрузка…
        </div>
      )}
      {error && (
        <div className="p-3">
          <div className="alert-danger text-xs">{error}</div>
          <Button size="sm" type="button" className="mt-2" onClick={onRetry}>
            Повторить
          </Button>
        </div>
      )}
      {!loading && !error && testCases.length === 0 && (
        <div className="p-6 text-center text-dim text-sm">Тест-кейсов пока нет.</div>
      )}
      {!loading && !error && testCases.length > 0 && (
        <div className="overflow-auto max-h-[320px]">
          <table className="mini w-full">
            <thead>
              <tr>
                <th>Код</th>
                <th>Название</th>
                <th>Zephyr ID</th>
                <th>Отдел</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {testCases.map((tc) => (
                <tr key={tc.id}>
                  <td className="mono">{tc.code}</td>
                  <td>{tc.title}</td>
                  <td className="mono">{tc.zephyr_id ?? "—"}</td>
                  <td className="mono">{tc.department_id ?? "—"}</td>
                  <td>
                    <div className="flex items-center gap-1 justify-end">
                      <Button size="sm" type="button" aria-label="Изменить" onClick={() => onEdit(tc)}>
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        type="button"
                        variant="danger"
                        aria-label="Удалить"
                        disabled={busyId === tc.id}
                        onClick={() => onDelete(tc)}
                      >
                        {busyId === tc.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StpTestCaseFormModal({
  mockMode,
  existing,
  defaultDepartmentId,
  departmentOptions,
  onClose,
  onDone,
}: {
  mockMode: boolean;
  existing: StpTestCase | null;
  defaultDepartmentId: string;
  departmentOptions: DropdownOption[];
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const isEdit = !!existing;
  const [loaded, setLoaded] = useState<StpTestCase | null>(existing);
  const [loading, setLoading] = useState(false);
  const [code, setCode] = useState(existing?.code ?? "");
  const [title, setTitle] = useState(existing?.title ?? "");
  const [zephyrId, setZephyrId] = useState(existing?.zephyr_id ?? "");
  const [departmentId, setDepartmentId] = useState(existing?.department_id ?? defaultDepartmentId ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // При открытии карточки на редактирование — подтягиваем свежие данные
  // (не доверяем кэшу списка), см. `getStpTestCase`.
  useEffect(() => {
    if (!existing || mockMode) return;
    let cancelled = false;
    setLoading(true);
    getStpTestCase(existing.id)
      .then((tc) => {
        if (cancelled) return;
        setLoaded(tc);
        setCode(tc.code);
        setTitle(tc.title);
        setZephyrId(tc.zephyr_id ?? "");
        setDepartmentId(tc.department_id ?? "");
      })
      .catch((e) => {
        if (!cancelled) setErr(apiErrMsg(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing?.id, mockMode]);

  async function submit() {
    if (!code.trim() || !title.trim()) {
      toast.warn("code и title обязательны");
      return;
    }
    if (mockMode) {
      toast.warn("Mock-режим — изменения не отправляются на backend.");
      onDone();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (isEdit && loaded) {
        const body: StpTestCaseUpdateRequest = {
          title: title.trim(),
          zephyr_id: zephyrId.trim() || null,
          department_id: departmentId || null,
        };
        await updateStpTestCase(loaded.id, body);
        toast.success("Тест-кейс обновлён");
      } else {
        const body: StpTestCaseCreateRequest = {
          code: code.trim(),
          title: title.trim(),
          zephyr_id: zephyrId.trim() || undefined,
          department_id: departmentId || undefined,
        };
        await createStpTestCase(body);
        toast.success("Тест-кейс создан");
      }
      onDone();
    } catch (e) {
      const msg = apiErrMsg(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={isEdit ? `Изменить тест-кейс ${existing?.code}` : "Новый тест-кейс СТП"}
    >
      <div className="grid gap-3">
        {loading && (
          <div className="text-xs text-dim inline-flex items-center gap-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> загрузка…
          </div>
        )}
        <label className="grid gap-1 text-xs text-dim">
          <span>code (join-ключ с test_definitions.code)</span>
          <input
            className="input mono"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="ASTRA-T101"
            disabled={isEdit}
          />
        </label>
        <label className="grid gap-1 text-xs text-dim">
          <span>title</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Установка с загрузочного носителя" />
        </label>
        <label className="grid gap-1 text-xs text-dim">
          <span>zephyr_id</span>
          <input className="input mono" value={zephyrId} onChange={(e) => setZephyrId(e.target.value)} placeholder="BT-T101 (необязательно)" />
        </label>
        <label className="grid gap-1 text-xs text-dim">
          <span>department_id</span>
          <Dropdown mode="single" options={departmentOptions} value={departmentId} onChange={setDepartmentId} searchable placeholder="Без отдела" />
        </label>
        {err && <div className="alert-danger text-xs">{err}</div>}
        <div className="flex gap-2 justify-end">
          <Button type="button" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button type="button" variant="primary" onClick={submit} disabled={busy || !code.trim() || !title.trim()}>
            {busy ? "..." : isEdit ? "Сохранить" : "Создать"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
