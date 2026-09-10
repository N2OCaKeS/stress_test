/**
 * Раздел «Отладка» — разовый запуск одного теста на одном стенде вне
 * большого fleet-прогона (debug-режим, `ALLTA MIGRATION.md` §5.5, переименовано
 * из legacy «dev mode»): тест не привязан к конкретному стенду
 * (`pinned_stand_id` снимается), поэтому доступны и физические, и виртуальные
 * стенды — в отличие от «Прогонов», где виртуальные стенды не участвуют.
 *
 * Средняя панель Shell — список запусков (тот же паттерн, что и
 * `RunsMiddlePanel`/`StpMiddlePanel`: поиск+фильтр+сортировка сверху не
 * скроллятся, список скроллится). Список остаётся demo-массивом
 * (`ADHOC_RUNS`) — backend `testing_service` пока не заводит публичный
 * эндпоинт ни для постановки debug-запуска в очередь (`services/queue.py::
 * enqueue(debug_mode=True, ...)` вызывается только из тестов сервиса, ни один
 * роутер его не вызывает), ни для истории таких запусков (нет `list
 * queue_items` вне контекста `test_run`/`test_stand`, см. `queueItems.ts`).
 * Кнопка «Запустить разовый тест» поэтому остаётся демо-заглушкой.
 *
 * Реальными стали данные лога выбранного запуска — рабочая зона трактует
 * `run.id` как `queue_item_id` и подключается к настоящему backend'у:
 * во время исполнения — `WS /queue-items/{id}/log/stream` (`logStream.ts`,
 * тот же канал, что и «Живой лог теста» в консоли сервера); после завершения
 * — `GET /queue-items/{id}/log/segments` + `GET /queue-items/{id}/log` для
 * навигации по чекпоинтам/командам с реальным текстом (сегменты бэкенд пишет
 * уже в legacy-формате `dev_libs`/`Libvit.py`: `TASK`/`STATUS`/`COMMAND`/
 * `CONCLUSION` — это буквальный текст внутри сегмента, реконструировать его
 * из отдельных полей не нужно).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bug, ChevronDown, ChevronUp, Download, Flag, Play, Radio, Search, Terminal } from "lucide-react";
import { naturalCompare } from "@/lib/naturalSort";
import { formatElapsedHMS, formatMsk, formatMskShort } from "@/lib/datetime";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { downloadTestLog, getTestLogText, listLogSegments } from "@/api/testing/testLogs";
import { testLogStreamProtocols, testLogStreamUrl } from "@/api/testing/logStream";
import type { TestLogSegment, TestLogSegmentStatus } from "@/api/testing/types";
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

/** Тикающий `Date.now()` раз в секунду — источник для realtime-элапсед-таймера, без опроса бэкенда. */
function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** Демо-`startedAt` хранится как `"DD.MM.YYYY HH:MM MSK"` — для elapsed-таймера достаточно локального парсинга, без строгой сверки часовых поясов. */
function demoStartedAtMs(startedAt: string): number | null {
  const match = startedAt.match(/(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2})/);
  if (!match) return null;
  const [, d, mo, y, h, mi] = match;
  return new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi)).getTime();
}

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
            toast.info(
              "Постановка разового debug-теста в очередь пока не выведена в публичный API testing_service (backend умеет debug_mode внутри, но эндпоинта для UI/CLI ещё нет) — кнопка остаётся демо-заглушкой",
            )
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

// ── поиск по тексту сегмента (общее для сайдбара-навигации и подсветки) ────

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

// ── живой лог во время исполнения (WS /queue-items/{id}/log/stream) ────────

type LiveConnState = "connecting" | "open" | "closed";

/**
 * Read-only живой лог: подключается сразу при монтировании (панель и так
 * открыта явным выбором running-запуска), без переподключения при закрытии
 * сервером — сообщение об этом дописывается в сам текст, как и в
 * `LiveTestLogTerminal` консоли сервера. В отличие от неё здесь нет xterm —
 * достаточно накапливаемого текста, страница и так рисует структурные логи
 * как текстовые блоки.
 */
function LiveLogPanel({ queueItemId }: { queueItemId: string }) {
  const [text, setText] = useState("");
  const [state, setState] = useState<LiveConnState>("connecting");
  const boxRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    setText("");
    setState("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(testLogStreamUrl(queueItemId), testLogStreamProtocols());
    } catch {
      setState("closed");
      return;
    }
    ws.onopen = () => setState("open");
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") setText((t) => t + ev.data);
    };
    ws.onclose = (ev) => {
      setState("closed");
      const tail = ev.reason === "TEST_FINISHED" ? "\n— тест завершён." : "\n— соединение закрыто.";
      setText((t) => t + tail);
    };
    ws.onerror = () => setState((s) => (s === "connecting" ? "closed" : s));
    return () => {
      ws.close(1000, "unmount");
    };
  }, [queueItemId]);

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [text]);

  const stateLabel: Record<LiveConnState, string> = {
    connecting: "Подключение…",
    open: "Живой лог подключён",
    closed: "Соединение закрыто",
  };

  return (
    <div className="surface border border-token rounded overflow-hidden flex flex-col min-h-[480px]">
      <div className="border-b border-token px-3 py-2 flex items-center gap-2 text-xs">
        <Radio className={`w-3.5 h-3.5 ${state === "open" ? "text-accent" : "text-dim"}`} />
        <span className={state === "open" ? "text-accent" : "text-dim"}>{stateLabel[state]}</span>
        <span className="text-dim ml-2">Только чтение — вывод исполняющегося сейчас теста</span>
      </div>
      <pre ref={boxRef} className="log-tail flex-1 overflow-auto m-0">{text || "Ожидаем вывод…"}</pre>
    </div>
  );
}

// ── завершённый лог: реальные сегменты + реальный текст (§2.6, §8 плана) ───

interface ResolvedLogSegment extends TestLogSegment {
  text: string;
}

/**
 * Тянет метаданные сегментов и полный текст лога отдельными запросами, затем
 * режет текст по `byte_offset_start/end` каждого сегмента на стороне клиента
 * — один `GET .../log` вместо N (по одному на сегмент). Офсеты в БД считаются
 * по байтам UTF-8, поэтому резка идёт по `Uint8Array`, не по JS-строке
 * (иначе многобайтовые символы в тексте лога сдвинули бы границы сегментов).
 */
function useFinishedLog(queueItemId: string) {
  const segmentsQ = useQuery(() => listLogSegments(queueItemId, { limit: 500 }), [queueItemId]);
  const textQ = useQuery(() => getTestLogText(queueItemId), [queueItemId]);

  const segments = useMemo<ResolvedLogSegment[]>(() => {
    const raw = segmentsQ.data?.items ?? [];
    const bytes = new TextEncoder().encode(textQ.data?.text ?? "");
    const decoder = new TextDecoder();
    return raw
      .slice()
      .sort((a, b) => a.position - b.position)
      .map((seg) => {
        const end = seg.byte_offset_end ?? bytes.length;
        const text = decoder.decode(bytes.slice(seg.byte_offset_start, end));
        return { ...seg, text };
      });
  }, [segmentsQ.data, textQ.data]);

  return {
    segments,
    loading: segmentsQ.loading || textQ.loading,
    error: segmentsQ.error ?? textQ.error,
    refetch: () => {
      segmentsQ.refetch();
      textQ.refetch();
    },
  };
}

const LOG_STATUS_BADGE: Record<TestLogSegmentStatus, BadgeKind> = { OK: "ok", CHANGED: "warn", FATAL: "danger" };

function logStatusBadge(status: string): BadgeKind {
  return LOG_STATUS_BADGE[status as TestLogSegmentStatus] ?? "warn";
}

function ResolvedLogBlock({
  seg,
  term,
  active,
  dimmed,
  refCallback,
}: {
  seg: ResolvedLogSegment;
  term: string;
  active: boolean;
  dimmed: boolean;
  refCallback: (el: HTMLDivElement | null) => void;
}) {
  const lines = (seg.text || "(пусто)").split("\n");
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
        <span className="text-[10px] text-dim mono">{formatMsk(seg.started_at)}</span>
        <Badge kind={logStatusBadge(seg.status)} className="ml-auto">{seg.status}</Badge>
      </div>
      <div className="mono text-[11px] leading-relaxed">
        {lines.map((line, i) => (
          <div key={i} className="break-all">
            <HighlightedLine text={line || " "} term={term} />
          </div>
        ))}
      </div>
    </div>
  );
}

/** Полноразмерный просмотр завершённого (`done`/`failed`) реального лога — навигация по чекпоинтам слева, поиск и скачивание. */
function FinishedLogViewer({ queueItemId }: { queueItemId: string }) {
  const toast = useToast();
  const { segments, loading, error, refetch } = useFinishedLog(queueItemId);
  const [searchTerm, setSearchTerm] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const blockRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  useEffect(() => {
    setSearchTerm("");
    setOnlyIssues(false);
    setActiveId(null);
  }, [queueItemId]);

  const term = searchTerm.trim();

  const matchesFilter = useCallback(
    (seg: ResolvedLogSegment) => {
      if (onlyIssues && seg.status === "OK") return false;
      if (term && countMatches(`${seg.label} ${seg.text}`, term) === 0) return false;
      return true;
    },
    [term, onlyIssues],
  );
  const isFilterActive = Boolean(term) || onlyIssues;
  const navSegments = useMemo(() => segments.filter(matchesFilter), [segments, matchesFilter]);

  const totalMatches = useMemo(() => {
    if (!term) return 0;
    return segments.reduce((sum, seg) => sum + countMatches(`${seg.label} ${seg.text}`, term), 0);
  }, [segments, term]);

  const scrollToSegment = (id: string) => {
    setActiveId(id);
    blockRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

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

  if (loading) {
    return <div className="surface border border-token rounded p-8 text-center text-dim">Загружаем лог…</div>;
  }
  if (error) {
    return (
      <div className="surface border border-token rounded p-8 text-center">
        <div className="text-danger text-xs mb-2">{apiErrMsg(error, "Лог не загрузился")}</div>
        <Button size="sm" type="button" onClick={refetch}>Повторить</Button>
      </div>
    );
  }

  return (
    <>
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
        <Button
          size="sm"
          type="button"
          className="ml-auto inline-flex items-center gap-1"
          onClick={handleDownload}
          disabled={downloading}
        >
          <Download className="w-3.5 h-3.5" />
          {downloading ? "Скачиваем…" : "Скачать лог"}
        </Button>
      </div>

      <div className="surface border border-token rounded overflow-hidden flex min-h-[480px]">
        <aside className="w-64 shrink-0 border-r border-token overflow-y-auto">
          {segments.length === 0 && <div className="p-4 text-xs text-dim text-center">Лог пуст</div>}
          {navSegments.map((seg) => (
            <button
              key={seg.id}
              type="button"
              onClick={() => scrollToSegment(seg.id)}
              className={`w-full text-left px-3 py-2 hover-bg flex items-start gap-2 border-l-2 ${
                activeId === seg.id ? "surface-2 border-accent" : "border-transparent"
              }`}
            >
              {seg.kind === "checkpoint" ? (
                <Flag className="w-3.5 h-3.5 mt-0.5 text-dim shrink-0" />
              ) : (
                <Terminal className="w-3.5 h-3.5 mt-0.5 text-dim shrink-0" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium truncate">{seg.label}</span>
                <span className="block text-[10px] text-dim mono truncate">{formatMskShort(seg.started_at)}</span>
              </span>
              <Badge kind={logStatusBadge(seg.status)} className="shrink-0">{seg.status}</Badge>
            </button>
          ))}
          {segments.length > 0 && navSegments.length === 0 && (
            <div className="p-4 text-xs text-dim text-center">Нет чекпоинтов по фильтру</div>
          )}
        </aside>

        <div className="flex-1 overflow-y-auto p-4 grid gap-3 content-start">
          {segments.map((seg) => (
            <ResolvedLogBlock
              key={seg.id}
              seg={seg}
              term={term}
              active={activeId === seg.id}
              dimmed={isFilterActive && !matchesFilter(seg)}
              refCallback={(el) => {
                if (el) blockRefs.current.set(seg.id, el);
                else blockRefs.current.delete(seg.id);
              }}
            />
          ))}
          {segments.length === 0 && <div className="text-xs text-dim italic px-1">Лог пока пуст</div>}
        </div>
      </div>
    </>
  );
}

// ── рабочая зона: заголовок запуска + живой/завершённый лог ────────────────

export function AdhocWorkzone({ state }: { state: AdhocState }) {
  const run = state.selectedRun;
  const now = useNow();

  if (!run) {
    return <div className="surface border border-token rounded p-8 text-center text-dim">Нет выбранного запуска</div>;
  }

  const meta = ADHOC_STATUS_META[run.status];
  const stand = standByName(run.standName);
  const startedMs = demoStartedAtMs(run.startedAt);

  return (
    <div className="grid gap-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-lg font-semibold mono">{run.id}</span>
            <Badge kind={meta.badge}>{meta.label}</Badge>
            {stand?.kind === "virtual" && <Badge kind="info">виртуальный стенд</Badge>}
            {run.status === "running" && startedMs !== null && (
              <span className="mono text-xs text-dim">прошло {formatElapsedHMS(now - startedMs)}</span>
            )}
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

      {run.status === "queued" && (
        <div className="surface border border-token rounded p-8 text-center text-dim text-sm">
          Тест поставлен в очередь, ожидает освобождения стенда — лога пока нет.
        </div>
      )}

      {run.status === "running" && <LiveLogPanel queueItemId={run.id} />}

      {(run.status === "done" || run.status === "failed") && <FinishedLogViewer queueItemId={run.id} />}
    </div>
  );
}
