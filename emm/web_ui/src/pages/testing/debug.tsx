/**
 * Раздел «Отладка» — разовый запуск одного теста на одном стенде вне
 * большого fleet-прогона. В плане переноса allta_app (`ALLTA MIGRATION.md`
 * §5.5) это называется debug-режим (переименовано из legacy «dev mode»):
 * тест не привязан к конкретному стенду (`pinned_stand_id` снимается),
 * поэтому доступны и физические, и виртуальные стенды — в отличие от
 * «Прогонов», где виртуальные стенды не участвуют (см. `stp.tsx`).
 *
 * Средняя панель Shell — список демо-запусков (тот же паттерн, что и
 * `RunsMiddlePanel`/`StpMiddlePanel`: поиск+фильтр+сортировка сверху не
 * скроллятся, список скроллится). Рабочая зона — полноразмерный просмотр
 * лога выбранного запуска: сайдбар-навигация по чекпоинтам/командам слева,
 * сам лог справа, с поиском и фильтром «только не-OK».
 *
 * Формат блока лога — по образцу `ALLTA MIGRATION.md` §8.1/§8.2 (перенос
 * формата `dev_libs`/`Libvit.py` 1:1): `TASK [label: stand]` / временная
 * метка / `STATUS [OK|CHANGED|FATAL]` / `COMMAND:` (с маскировкой
 * `is_sensitive`-аргументов) / `CONCLUSION:`. Реального `test_log_segments`
 * с офсетами в БД тут нет — сегменты собраны заранее в demo-массив, но
 * структура (чекпоинт/команда + переход по клику) воспроизводит целевой UX.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bug, ChevronDown, ChevronUp, Flag, Play, Search, Terminal } from "lucide-react";
import { naturalCompare } from "@/lib/naturalSort";
import { useToast } from "@/contexts/ToastContext";
import { TEST_CATALOG } from "./tests";
import { STANDS, type BadgeKind } from "./_shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";

export type AdhocStatus = "queued" | "running" | "done" | "failed";
export type AdhocMode = "orel" | "smolensk";

export interface AdhocRun {
  id: string;
  testCode: string;
  standName: string;
  standId: number;
  mode: AdhocMode;
  status: AdhocStatus;
  startedAt: string;
}

function testFullName(code: string): string {
  return TEST_CATALOG.find((t) => t.code === code)?.fullName ?? code;
}

function standByName(name: string) {
  return STANDS.find((s) => s.name === name);
}

export const ADHOC_RUNS: AdhocRun[] = [
  { id: "adhoc-2026090701", testCode: "STR-SEGFAULT-FUZZ", standName: "stand15-110", standId: 15, mode: "orel", status: "running", startedAt: "07.09.2026 09:40 MSK" },
  { id: "adhoc-2026090612", testCode: "DB-PG-TPCC", standName: "vm-stand1", standId: 21, mode: "smolensk", status: "done", startedAt: "06.09.2026 22:10 MSK" },
  { id: "adhoc-2026090605", testCode: "SEC-FREEIPA-JOIN", standName: "stand8-103", standId: 8, mode: "orel", status: "failed", startedAt: "06.09.2026 18:05 MSK" },
  { id: "adhoc-2026090520", testCode: "FS-XFS-FILL", standName: "stand6-101", standId: 6, mode: "smolensk", status: "done", startedAt: "05.09.2026 20:12 MSK" },
  { id: "adhoc-2026090511", testCode: "NET-IPERF3", standName: "vm-stand2", standId: 22, mode: "orel", status: "queued", startedAt: "05.09.2026 11:00 MSK" },
  { id: "adhoc-2026090409", testCode: "OTH-UNIXBENCH", standName: "stand14-109", standId: 14, mode: "smolensk", status: "done", startedAt: "04.09.2026 09:47 MSK" },
  { id: "adhoc-2026090318", testCode: "STR-OOM-KILLER", standName: "stand10-105", standId: 10, mode: "orel", status: "failed", startedAt: "03.09.2026 18:30 MSK" },
  { id: "adhoc-2026090215", testCode: "DB-SYSBENCH-OLTP", standName: "stand17-112", standId: 17, mode: "smolensk", status: "done", startedAt: "02.09.2026 15:05 MSK" },
  { id: "adhoc-2026090108", testCode: "SEC-IPTABLES", standName: "stand20-115", standId: 20, mode: "orel", status: "done", startedAt: "01.09.2026 08:22 MSK" },
];

const ADHOC_STATUS_META: Record<AdhocStatus, { label: string; badge: BadgeKind }> = {
  queued: { label: "В очереди", badge: "warn" },
  running: { label: "Выполняется", badge: "accent" },
  done: { label: "Завершён", badge: "ok" },
  failed: { label: "Провален", badge: "danger" },
};

const STATUS_FILTER_OPTIONS: (AdhocStatus | "all")[] = ["all", "queued", "running", "done", "failed"];

// ── состояние средней панели, общее для AdhocMiddlePanel и AdhocWorkzone ────

export interface AdhocState {
  runs: AdhocRun[];
  total: number;
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

export function useAdhocState(): AdhocState {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<AdhocStatus | "all">("all");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedId, setSelectedId] = useState<string>(ADHOC_RUNS[0]?.id ?? "");

  const runs = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = ADHOC_RUNS.filter((run) => {
      if (statusFilter !== "all" && run.status !== statusFilter) return false;
      if (!term) return true;
      return (
        run.id.toLowerCase().includes(term) ||
        run.testCode.toLowerCase().includes(term) ||
        run.standName.toLowerCase().includes(term)
      );
    });
    return [...filtered].sort((a, b) =>
      sortDir === "asc" ? naturalCompare(a.id, b.id) : naturalCompare(b.id, a.id),
    );
  }, [search, statusFilter, sortDir]);

  const selectedRun = ADHOC_RUNS.find((r) => r.id === selectedId) ?? ADHOC_RUNS[0] ?? null;

  return {
    runs,
    total: ADHOC_RUNS.length,
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

// ── средняя панель Shell: список разовых запусков ───────────────────────────

export function AdhocMiddlePanel({ state }: { state: AdhocState }) {
  const toast = useToast();
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
        {state.runs.length === 0 && <div className="px-3 py-6 text-xs text-dim text-center">Нет запусков по фильтру</div>}
        {state.runs.map((run) => (
          <AdhocListRow key={run.id} run={run} active={run.id === state.selectedId} onSelect={() => state.setSelectedId(run.id)} />
        ))}
      </div>

      <div className="border-t border-token p-3 shrink-0">
        <Button
          variant="primary"
          size="sm"
          type="button"
          className="w-full flex items-center justify-center gap-2"
          onClick={() =>
            toast.info("Демо: запуск одиночного теста в debug-режиме — привязка к стенду снята, лог откроется в рабочей зоне сразу после старта")
          }
        >
          <Play className="w-3.5 h-3.5" />
          Запустить разовый тест
        </Button>
      </div>
    </aside>
  );
}

function AdhocListRow({ run, active, onSelect }: { run: AdhocRun; active: boolean; onSelect: () => void }) {
  const meta = ADHOC_STATUS_META[run.status];
  const stand = standByName(run.standName);
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
        {stand?.kind === "virtual" && <Badge kind="info" className="shrink-0">ВМ</Badge>}
        <Badge kind={meta.badge} className="shrink-0 ml-auto">{meta.label}</Badge>
      </div>
      <div className="text-xs truncate" title={testFullName(run.testCode)}>{run.testCode}</div>
      <div className="mono text-[11px] text-dim truncate">{run.standName} · {run.mode} · {run.startedAt}</div>
    </button>
  );
}

// ── формат блока лога (ALLTA MIGRATION §8.1/§8.2) ───────────────────────────

export type AdhocLogStatus = "OK" | "CHANGED" | "FATAL";
type AdhocSegmentKind = "checkpoint" | "command";

interface AdhocLogSegment {
  idx: number;
  kind: AdhocSegmentKind;
  label: string;
  time: string;
  status: AdhocLogStatus;
  command: string;
  conclusion: string;
}

const LOG_STATUS_BADGE: Record<AdhocLogStatus, BadgeKind> = { OK: "ok", CHANGED: "warn", FATAL: "danger" };

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

interface StartedParts {
  day: number;
  month: number;
  year: number;
  hour: number;
  minute: number;
}

function parseStarted(startedAt: string): StartedParts {
  const match = startedAt.match(/(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2})/);
  if (!match) return { day: 1, month: 1, year: 2026, hour: 0, minute: 0 };
  const [, d, mo, y, h, mi] = match;
  return { day: Number(d), month: Number(mo), year: Number(y), hour: Number(h), minute: Number(mi) };
}

/** Время сегмента — смещение в минутах от старта запуска, секунды — по псевдослучайному сиду. */
function segmentTime(base: StartedParts, offsetMinutes: number, secondsSeed: number): string {
  const totalMinutes = base.hour * 60 + base.minute + offsetMinutes;
  const hour = Math.floor(totalMinutes / 60) % 24;
  const minute = totalMinutes % 60;
  const second = (secondsSeed * 7) % 60;
  return `${pad2(hour)}:${pad2(minute)}:${pad2(second)} ${pad2(base.day)}-${pad2(base.month)}-${base.year} MSK`;
}

function seedOf(run: AdhocRun): number {
  let sum = 0;
  for (const ch of run.id) sum += ch.charCodeAt(0);
  return sum;
}

/**
 * Демо-сегменты лога для выбранного запуска — по мотивам реального потока
 * `prepare-for-test` (§5.1): подготовка стенда → учётка тестового
 * пользователя → установка теста → сам тест → сбор артефактов. Один из
 * командных сегментов маскирует пароль (`is_sensitive`) в `COMMAND:`, чтобы
 * демонстрировать требуемое поведение, а не просто описывать его.
 */
function buildAdhocLog(run: AdhocRun): { segments: AdhocLogSegment[]; tailNote: string | null } {
  const test = TEST_CATALOG.find((t) => t.code === run.testCode);
  const base = parseStarted(run.startedAt);
  const seed = seedOf(run);
  const time = (offsetMinutes: number, secondsSeed: number) => segmentTime(base, offsetMinutes, secondsSeed);

  const prepare: AdhocLogSegment = {
    idx: 0,
    kind: "checkpoint",
    label: "prepare-stand",
    time: time(0, 3),
    status: "OK",
    command: `ssh -o BatchMode=yes u@${run.standName} 'mkdir -p /opt/allta/work'`,
    conclusion: "рабочий каталог создан, окружение проверено",
  };
  const bootstrap: AdhocLogSegment = {
    idx: 1,
    kind: "command",
    label: "bootstrap-account",
    time: time(1, 5),
    status: seed % 2 === 0 ? "CHANGED" : "OK",
    command: `useradd -m u 2>/dev/null; printf 'u:%s' "***" | chpasswd`,
    conclusion: "тестовый пользователь u готов, пароль установлен (замаскирован в логе, is_sensitive=true)",
  };
  const install: AdhocLogSegment = {
    idx: 2,
    kind: "command",
    label: "install-test-package",
    time: time(2, 7),
    status: "OK",
    command: `apt-get install -y ${run.testCode.toLowerCase()}`,
    conclusion: `пакет установлен, параметры запуска: ${test?.params ?? "по умолчанию"}`,
  };

  if (run.status === "queued") {
    return { segments: [], tailNote: "тест поставлен в очередь, ожидает освобождения стенда" };
  }
  if (run.status === "running") {
    return { segments: [prepare, bootstrap, install], tailNote: "тест выполняется, лог обновляется…" };
  }

  const runTest: AdhocLogSegment = {
    idx: 3,
    kind: "checkpoint",
    label: "run-test",
    time: time(4, 11),
    status: run.status === "failed" ? "FATAL" : seed % 3 === 0 ? "CHANGED" : "OK",
    command: `python3 -m allta.tests.${run.testCode.toLowerCase().replace(/-/g, "_")} --stand ${run.standName} --mode ${run.mode} --user u --password ***`,
    conclusion:
      run.status === "failed"
        ? `${test?.fullName ?? run.testCode} завершился с ошибкой, см. stderr`
        : `${test?.fullName ?? run.testCode} выполнен успешно`,
  };

  if (run.status === "failed") {
    return { segments: [prepare, bootstrap, install, runTest], tailNote: null };
  }

  const collect: AdhocLogSegment = {
    idx: 4,
    kind: "command",
    label: "collect-artifacts",
    time: time(5, 13),
    status: "OK",
    command: `scp u@${run.standName}:/opt/allta/work/result.json ./artifacts/`,
    conclusion: "лог и артефакты сохранены",
  };
  const finish: AdhocLogSegment = {
    idx: 5,
    kind: "checkpoint",
    label: "finish",
    time: time(6, 17),
    status: "OK",
    command: "-",
    conclusion: "разовый запуск завершён",
  };

  return { segments: [prepare, bootstrap, install, runTest, collect, finish], tailNote: null };
}

function countMatches(text: string, term: string): number {
  if (!term) return 0;
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(escaped, "gi");
  return (text.match(re) || []).length;
}

function HighlightedLine({ text, term }: { text: string; term: string }) {
  if (!term) return <>{text}</>;
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="bg-[var(--warn)] text-black rounded px-0.5">{part}</mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function AdhocLogBlock({
  seg,
  standName,
  term,
  active,
  dimmed,
  refCallback,
}: {
  seg: AdhocLogSegment;
  standName: string;
  term: string;
  active: boolean;
  dimmed: boolean;
  refCallback: (el: HTMLDivElement | null) => void;
}) {
  const frame = (seg.status === "FATAL" ? "#" : "*").repeat(66);
  const lines = [
    frame,
    `TASK [${seg.label}: ${standName}]`,
    `[ ${seg.time} ]`,
    `STATUS [${seg.status}]`,
    `COMMAND: ${seg.command}`,
    "",
    `CONCLUSION: ${seg.conclusion}`,
    frame,
  ];
  return (
    <div
      ref={refCallback}
      className={`surface-2 border rounded p-3 transition-opacity ${active ? "border-accent" : "border-token"} ${
        dimmed ? "opacity-40" : ""
      }`}
    >
      <div className="flex items-center gap-2 mb-2">
        {seg.kind === "checkpoint" ? <Flag className="w-3.5 h-3.5 text-dim" /> : <Terminal className="w-3.5 h-3.5 text-dim" />}
        <span className="text-xs font-medium">{seg.label}</span>
        <Badge kind={LOG_STATUS_BADGE[seg.status]} className="ml-auto">{seg.status}</Badge>
      </div>
      <div className="mono text-[11px] leading-relaxed">
        {lines.map((line, i) => (
          <div key={i} className="break-all">
            <HighlightedLine text={line || " "} term={term} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ── рабочая зона: полноразмерный просмотр лога с навигацией и поиском ──────

export function AdhocWorkzone({ state }: { state: AdhocState }) {
  const run = state.selectedRun;
  const [searchTerm, setSearchTerm] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const blockRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  useEffect(() => {
    setSearchTerm("");
    setOnlyIssues(false);
    setActiveIdx(null);
  }, [run?.id]);

  const { segments, tailNote } = useMemo(() => (run ? buildAdhocLog(run) : { segments: [], tailNote: null }), [run]);

  const term = searchTerm.trim();

  const matchesFilter = useCallback(
    (seg: AdhocLogSegment) => {
      if (onlyIssues && seg.status === "OK") return false;
      if (term && countMatches(`${seg.label} ${seg.command} ${seg.conclusion}`, term) === 0) return false;
      return true;
    },
    [term, onlyIssues],
  );
  const isFilterActive = Boolean(term) || onlyIssues;
  const navSegments = useMemo(() => segments.filter(matchesFilter), [segments, matchesFilter]);

  const totalMatches = useMemo(() => {
    if (!term) return 0;
    return segments.reduce((sum, seg) => sum + countMatches(`${seg.label} ${seg.command} ${seg.conclusion}`, term), 0);
  }, [segments, term]);

  const scrollToSegment = (idx: number) => {
    setActiveIdx(idx);
    blockRefs.current.get(idx)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (!run) {
    return <div className="surface border border-token rounded p-8 text-center text-dim">Нет выбранного запуска</div>;
  }

  const meta = ADHOC_STATUS_META[run.status];
  const stand = standByName(run.standName);

  return (
    <div className="grid gap-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg font-semibold mono">{run.id}</span>
            <Badge kind={meta.badge}>{meta.label}</Badge>
            {stand?.kind === "virtual" && <Badge kind="info">виртуальный стенд</Badge>}
          </div>
          <div className="text-xs text-dim mt-1">
            {run.testCode} · {testFullName(run.testCode)} · {run.standName} · режим {run.mode} · запущен {run.startedAt}
          </div>
        </div>
      </div>

      <div className="alert-warn text-xs">
        <Bug className="w-3.5 h-3.5 shrink-0" />
        <span>
          Разовый запуск вне прогона (debug-режим) — привязка к стенду снята, поэтому доступны и физические, и
          виртуальные стенды. В «Прогонах» (fleet-wide) такие запуски не участвуют.
        </span>
      </div>

      <div className="surface border border-token rounded p-3 flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1 min-w-[240px]">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder="Поиск по логу…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        {term && <span className="text-xs text-dim">{totalMatches} совпадений</span>}
        <Checkbox checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} label="Только не-OK (CHANGED/FATAL)" />
      </div>

      <div className="surface border border-token rounded overflow-hidden flex min-h-[480px]">
        <aside className="w-64 shrink-0 border-r border-token overflow-y-auto">
          {segments.length === 0 && (
            <div className="p-4 text-xs text-dim text-center">{tailNote ?? "Лог пуст"}</div>
          )}
          {navSegments.map((seg) => (
            <button
              key={seg.idx}
              type="button"
              onClick={() => scrollToSegment(seg.idx)}
              className={`w-full text-left px-3 py-2 hover-bg flex items-start gap-2 border-l-2 ${
                activeIdx === seg.idx ? "surface-2 border-accent" : "border-transparent"
              }`}
            >
              {seg.kind === "checkpoint" ? (
                <Flag className="w-3.5 h-3.5 mt-0.5 text-dim shrink-0" />
              ) : (
                <Terminal className="w-3.5 h-3.5 mt-0.5 text-dim shrink-0" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium truncate">{seg.label}</span>
                <span className="block text-[10px] text-dim mono truncate">{seg.time}</span>
              </span>
              <Badge kind={LOG_STATUS_BADGE[seg.status]} className="shrink-0">{seg.status}</Badge>
            </button>
          ))}
          {segments.length > 0 && navSegments.length === 0 && (
            <div className="p-4 text-xs text-dim text-center">Нет чекпоинтов по фильтру</div>
          )}
        </aside>

        <div className="flex-1 overflow-y-auto p-4 grid gap-3 content-start">
          {segments.map((seg) => (
            <AdhocLogBlock
              key={seg.idx}
              seg={seg}
              standName={run.standName}
              term={term}
              active={activeIdx === seg.idx}
              dimmed={isFilterActive && !matchesFilter(seg)}
              refCallback={(el) => {
                if (el) blockRefs.current.set(seg.idx, el);
                else blockRefs.current.delete(seg.idx);
              }}
            />
          ))}
          {tailNote && <div className="text-xs text-dim italic px-1">{tailNote}</div>}
          {segments.length === 0 && !tailNote && <div className="text-xs text-dim italic px-1">Лог пока пуст</div>}
        </div>
      </div>
    </div>
  );
}
