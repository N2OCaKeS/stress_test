/** Просмотр лога одной попытки: live-поток, сегменты и скачивание. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, Flag, Radio, Search, Terminal } from "lucide-react";
import { formatMsk, formatMskShort } from "@/lib/datetime";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { downloadTestLog, getTestLogText, listLogSegments } from "@/api/testing/testLogs";
import { testLogStreamProtocols, testLogStreamUrl } from "@/api/testing/logStream";
import type { TestLogSegment, TestLogSegmentStatus } from "@/api/testing/types";
import { type BadgeKind } from "./_shared";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";

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

/** Офсеты backend — индексы Unicode code points в Python-строке. */
function useFinishedLog(queueItemId: string) {
  const segmentsQ = useQuery(async () => {
    const segments: TestLogSegment[] = [];
    let total = 0;
    do {
      const page = await listLogSegments(queueItemId, { limit: 500, offset: segments.length });
      segments.push(...page.items);
      total = page.total;
      if (page.items.length === 0) break;
    } while (segments.length < total);
    return { items: segments };
  }, [queueItemId]);
  const textQ = useQuery(() => getTestLogText(queueItemId), [queueItemId]);

  const segments = useMemo<ResolvedLogSegment[]>(() => {
    const raw = segmentsQ.data?.items ?? [];
    const characters = Array.from(textQ.data?.text ?? "");
    return raw
      .slice()
      .sort((a, b) => a.position - b.position)
      .map((seg) => {
        const end = seg.byte_offset_end ?? characters.length;
        const text = characters.slice(seg.byte_offset_start, end).join("");
        return { ...seg, text };
      });
  }, [segmentsQ.data, textQ.data]);

  return {
    segments,
    text: textQ.data?.text ?? "",
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
  const { segments, text, loading, error, refetch } = useFinishedLog(queueItemId);
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
          {segments.length === 0 && <pre className="log-tail whitespace-pre-wrap">{text || "Лог пока пуст"}</pre>}
        </div>
      </div>
    </>
  );
}

export function AttemptLogViewer({ queueItemId, state }: { queueItemId: string; state: string }) {
  if (["queued", "preparing", "ready"].includes(state)) {
    return <div className="surface border border-token rounded p-8 text-center text-dim text-sm">
      Задание в очереди или на подготовке стенда — лог исполнения пока недоступен.
    </div>;
  }
  return state === "running"
    ? <LiveLogPanel key={queueItemId} queueItemId={queueItemId} />
    : <FinishedLogViewer key={queueItemId} queueItemId={queueItemId} />;
}
