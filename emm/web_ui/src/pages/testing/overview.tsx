/**
 * «Рабочая зона» тестирования — обзор пула (§F плана 2026-09-11) + список
 * стендов + управление очередью стенда. Три визуальных концепта одного и того
 * же списка стендов (карточки/полоски/очереди) сохранены как есть — это
 * материал для согласования с руководителем, какой вид удобнее для
 * повседневной работы; по умолчанию открываются «Полоски».
 *
 * Обзор пула (`PoolOverviewPanel`) — реальные агрегаты `testing_service`
 * (`GET /pool-overview`): очередь/исходы по последней попытке логического
 * теста и статусы стендов (восстановление/недоступность/тест/готовность) из
 * живого ping/busy server_service.
 *
 * Источник стендов ниже — `testing_service` (`listTestStands`/`getTestStand`,
 * живая карточка сервера приходит вложенной в ответ `getTestStand`); очередь
 * стенда — реальные элементы `GET /queue-items`, сгруппированные по стенду.
 * Управление очередью («Пропустить»/«Остановить»/«Продолжить») ходит в
 * `POST /queue-items/{id}/skip|pause` и `POST /test-stands/{id}/resume-queue`.
 * Пока прерывание не подтвердил воркер, активный item несёт
 * `interrupt_action` — кнопки на это время дизейблятся.
 *
 * В mock-режиме (`VITE_USE_MOCK_AUTH=true`) страница по-прежнему работает на
 * demo-данных `_shared.tsx`; управление очередью там не показывается — за ним
 * нет ни реальных id, ни бэкенда.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Cpu,
  ExternalLink,
  Gauge,
  HelpCircle,
  LayoutGrid,
  ListChecks,
  ListTree,
  Pause,
  Play,
  RefreshCcw,
  RotateCw,
  Server,
  SkipForward,
  Thermometer,
} from "lucide-react";
import { LaunchRunModal, type RunsState } from "./runs";
import { Dropdown, type DropdownOption } from "@/components/ui/Dropdown";
import {
  Counter,
  EmptySearch,
  InfoBox,
  KnownIssueBadge,
  LoadMeter,
  LogViewerModal,
  MetaRow,
  OS_VERSIONS,
  Stat,
  StatusBadge,
  STANDS,
  STATUS_META,
  QUEUE_TEXT,
  currentQueueItem,
  demoQueueLog,
  queueBadge,
  queueStats,
  type QueueItem,
  type QueueState,
  type Stand,
  type StandMetrics,
  type StandStatus,
} from "./_shared";
import { AttemptLogViewer } from "./AttemptLogViewer";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { getTestStand, listTestStands } from "@/api/testing/testStands";
import {
  launchQueueItem,
  listQueueItems,
  pauseQueueItem,
  resumeStandQueue,
  retryQueueItem,
  skipQueueItem,
  type PublicQueueItem,
  type QueueInterruptAction,
} from "@/api/testing/queueItems";
import { listTestDefinitions } from "@/api/testing/testDefinitions";
import type { TestDefinition, TestRun, TestStand } from "@/api/testing/types";
import { listOsVersions } from "@/api/server/osVersions";
import { getPoolOverview } from "@/api/testing/poolOverview";
import type {
  PoolOverviewContext,
  PoolOverviewResponse,
  PoolStandStatus,
} from "@/api/testing/types";

type ConceptId = "cards" | "strips" | "queue";
type LaunchModal = "test" | "run" | null;
type StandFilter = "all" | "testing" | "busy";

interface LogTarget {
  stand: Stand;
  item?: QueueItem;
}

/** Опция выпадашки РЦ вместе с ядрами, которые на ней доступны. */
interface RcOption {
  value: string;
  label: string;
  kernels: string[];
}

/** Тест, доступный для постановки в очередь: реальный каталог или demo-список. */
interface LaunchableTest {
  id: string;
  label: string;
  /** Стенд, за которым тест закреплён (`pinned_stand_id`), если закреплён. */
  standKey: string | null;
}

// ── живые данные testing_service ────────────────────────────────────────────

/** Поля живой карточки сервера (`TestStand.server`), нужные этой странице. */
interface LiveServerCard {
  hostname?: string | null;
  display_name?: string | null;
  ip_address?: string | null;
  os_version_id?: string | null;
  busy_state?: string | null;
  busy_note?: string | null;
}

function asServerCard(server: Record<string, unknown> | null): LiveServerCard | null {
  return server as LiveServerCard | null;
}

function hashSeed(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return hash;
}

function clampPct(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

/**
 * `testing_service`/`server_service` пока не отдают отдельную live-метрику
 * загрузки стенда (CPU/RAM/температура) — только идентичность и `busy_state`.
 * Значения ниже — детерминированная (по id стенда) заглушка, чтобы дашборд
 * не пустовал визуально до появления реального телеметрического эндпоинта.
 */
function placeholderMetrics(seed: number, status: StandStatus): StandMetrics {
  if (status === "offline") {
    return { cpuUser: 0, cpuSystem: 0, cpuTemp: 0, ram: 0, diskNvme: 0, diskSda: 0, history: Array(12).fill(0) };
  }
  const base = status === "idle" ? 6 + (seed % 12) : status === "manual" ? 18 + (seed % 30) : 28 + (seed % 60);
  const cpuUser = clampPct(base * 0.65);
  const cpuSystem = clampPct(Math.max(base - cpuUser, 2));
  const ram = status === "idle" ? 20 + (seed % 20) : status === "manual" ? 30 + (seed % 35) : 38 + (seed % 50);
  const cpuTemp = 38 + (seed % 30);
  const diskNvme = 20 + (seed % 55);
  const diskSda = 10 + (seed % 40);
  const history = Array.from({ length: 12 }, (_, i) => clampPct(base + Math.sin(i * 1.3 + seed) * 12));
  return { cpuUser, cpuSystem, cpuTemp, ram, diskNvme, diskSda, history };
}

function mapQueueItemState(state: string): QueueState {
  if (state === "succeeded") return "done";
  if (state === "failed") return "failed";
  if (state === "skipped") return "skipped";
  if (state === "paused") return "paused";
  if (state === "queued") return "pending";
  return "running"; // preparing | ready | running
}

const QUEUE_STATE_NOTE: Record<string, string> = {
  queued: "ожидает своей очереди",
  preparing: "стенд готовится к тесту",
  ready: "стенд готов, ждём воркер",
  running: "выполняется на стенде",
  paused: "остановлен, ждёт продолжения очереди",
  skipped: "пропущен",
  succeeded: "завершён успешно",
  failed: "завершён с ошибкой",
};

/** Состояния, в которых item считается активной работой стенда. */
const ACTIVE_QUEUE_STATES = ["queued", "preparing", "ready", "running"];

const TERMINAL_QUEUE_STATES = ["succeeded", "failed", "skipped"];

function itemTitle(item: PublicQueueItem): string {
  return item.test_code || item.test_name || item.test_id;
}

/** `PublicQueueItem` → презентационный элемент очереди. */
function toQueueItem(item: PublicQueueItem): QueueItem {
  const note = QUEUE_STATE_NOTE[item.state] ?? item.state;
  const interrupt = item.interrupt_action ?? null;
  return {
    title: itemTitle(item),
    state: mapQueueItemState(item.state),
    meta: interrupt
      ? interrupt === "skip"
        ? "прерывается, будет пропущен"
        : "прерывается, встанет на паузу"
      : item.error
        ? `${note} · ${item.error}`
        : note,
    itemId: item.id,
    rawState: item.state,
    logStatus: item.log_status,
    interruptAction: interrupt,
    canRetry: TERMINAL_QUEUE_STATES.includes(item.state) && item.is_current !== false,
  };
}

interface StandQueue {
  active: PublicQueueItem | null;
  paused: PublicQueueItem | null;
}

/** Загружает список стендов + живую карточку сервера на каждый. */
async function fetchLiveStands(): Promise<TestStand[]> {
  const list = await listTestStands({ limit: 500 });
  return Promise.all(list.items.map((item) => getTestStand(item.id)));
}

/**
 * Активный и остановленный элементы очереди каждого стенда — двумя списочными
 * запросами на весь пул, а не поштучным `current-queue-item` на стенд.
 * `current-queue-item` отдаёт урезанную сводку без `interrupt_action`, а он
 * здесь нужен: пока прерывание не подтверждено воркером, кнопки дизейблятся.
 *
 * Фильтр по `paused` отваливается на бэкенде, который ещё не знает этого
 * состояния — тогда считаем, что остановленных элементов нет, и не роняем
 * всю страницу.
 */
async function fetchStandQueues(): Promise<Map<string, StandQueue>> {
  const [activePage, pausedPage] = await Promise.all([
    listQueueItems({ kind: "all", states: ACTIVE_QUEUE_STATES, order: "asc", limit: 500 }),
    listQueueItems({ kind: "all", states: ["paused"], order: "asc", limit: 500 }).catch(() => null),
  ]);
  const byStand = new Map<string, StandQueue>();
  const slot = (standId: string): StandQueue => {
    const existing = byStand.get(standId);
    if (existing) return existing;
    const fresh: StandQueue = { active: null, paused: null };
    byStand.set(standId, fresh);
    return fresh;
  };
  for (const item of activePage.items) {
    const entry = slot(item.stand_id);
    // Список отсортирован по created_at asc — первым идёт тот, что раньше
    // поставлен в очередь; running всегда выигрывает у ждущих.
    if (!entry.active || item.state === "running") entry.active = item;
  }
  for (const item of pausedPage?.items ?? []) {
    const entry = slot(item.stand_id);
    if (!entry.paused) entry.paused = item;
  }
  return byStand;
}

/** Строит `Stand[]` (совместимый с demo-типом из `_shared.tsx`) из живых данных testing_service. */
function mapLiveStands(
  data: TestStand[],
  queues: Map<string, StandQueue>,
  osNameById: Map<string, string>,
): Stand[] {
  return data.map((stand, index) => {
    const server = asServerCard(stand.server);
    const unavailable = stand.server_unavailable || !server;
    const busy = server?.busy_state ?? null;
    const status: StandStatus = unavailable
      ? "offline"
      : busy === "testing"
        ? "testing"
        : busy === "free" || busy == null
          ? "idle"
          : "manual";
    const name = server?.display_name || server?.hostname || stand.server_id;
    const ip = server?.ip_address || "—";
    const os = server?.os_version_id ? osNameById.get(server.os_version_id) ?? "—" : "—";
    const standQueue = queues.get(stand.id) ?? { active: null, paused: null };
    const primary = standQueue.active ?? standQueue.paused;
    const queue: QueueItem[] = [standQueue.paused, standQueue.active]
      .filter((item): item is PublicQueueItem => item !== null)
      .map(toQueueItem);
    const currentTitle = primary
      ? itemTitle(primary)
      : status === "manual"
        ? server?.busy_note || "ручная работа"
        : "Нет активной работы";
    const currentMeta = primary
      ? queue.find((item) => item.itemId === primary.id)?.meta ?? primary.state
      : status === "offline"
        ? "стенд недоступен"
        : status === "manual"
          ? "занят вне testing_service"
          : "стенд свободен";
    return {
      id: index + 1,
      name,
      ip,
      status,
      os,
      kernel: primary?.kernel || "—",
      currentTitle,
      currentMeta,
      metrics: placeholderMetrics(hashSeed(stand.id), status),
      queue,
      live: {
        standId: stand.id,
        activeItemId: standQueue.active?.id ?? null,
        activeState: standQueue.active?.state ?? null,
        pausedItemId: standQueue.paused?.id ?? null,
        interruptAction: standQueue.active?.interrupt_action ?? null,
      },
    };
  });
}

/** Ключ, по которому тест считается закреплённым за стендом. */
function standKey(stand: Stand): string {
  return stand.live?.standId ?? String(stand.id);
}

// ── управление очередью стенда ──────────────────────────────────────────────

interface QueueControls {
  /** Стенд, по которому сейчас летит запрос — его кнопки заблокированы. */
  pendingStandId: string | null;
  skip: (stand: Stand) => void;
  pause: (stand: Stand) => void;
  resume: (stand: Stand) => void;
}

/**
 * Текст подтверждения для «Пропустить»/«Остановить». Обе кнопки живут сразу в
 * двух местах (полоска стенда и модалка очереди), но ходят через один и тот же
 * `queueControls`, поэтому формулировка описана здесь один раз — иначе она
 * неизбежно разъедется между вызовами.
 */
function interruptConfirmOptions(action: QueueInterruptAction, standName: string) {
  const common = `Исполняющийся тест на стенде «${standName}» будет принудительно убит на железе. Уже проделанная им работа пропадёт, вернуть её нельзя.`;
  return action === "skip"
    ? {
        title: "Пропустить тест",
        message: `${common}\n\nСтенд сразу перейдёт к следующему элементу очереди.`,
        confirmLabel: "Пропустить",
        danger: true,
      }
    : {
        title: "Остановить тест",
        message: `${common}\n\nСтенд встанет и не возьмёт следующий элемент, пока не нажать «Продолжить».`,
        confirmLabel: "Остановить",
        danger: true,
      };
}

/**
 * Кнопки управления очередью стенда. Показываются только на живых данных:
 * demo-стенды не несут реальных id, управлять там нечем.
 */
function StandQueueActions({
  stand,
  controls,
  swallowEvents,
  scope = "all",
}: {
  stand: Stand;
  controls: QueueControls;
  /** Кнопки внутри `<summary>` — клик не должен схлопывать/раскрывать полоску. */
  swallowEvents?: boolean;
  /** Ограничить набор кнопок одним элементом очереди — нужно в списке очереди. */
  scope?: "all" | "active" | "paused";
}) {
  const live = stand.live;
  if (!live || (!live.activeItemId && !live.pausedItemId)) return null;
  const showActive = scope !== "paused" && live.activeItemId !== null;
  const showPaused = scope !== "active" && live.pausedItemId !== null;
  const busy = controls.pendingStandId === live.standId;
  const interrupting = live.interruptAction !== null;
  const guard = (action: () => void) => (event: React.MouseEvent) => {
    if (swallowEvents) {
      event.preventDefault();
      event.stopPropagation();
    }
    action();
  };
  return (
    <>
      {interrupting && showActive && <span className="text-xs text-warn whitespace-nowrap">Останавливается…</span>}
      {showActive && (
        <>
          <Button
            size="sm"
            type="button"
            className="inline-flex items-center gap-1"
            disabled={busy || interrupting}
            onClick={guard(() => controls.skip(stand))}
            title="Прервать текущий тест и перейти к следующему в очереди стенда"
          >
            <SkipForward className="w-3.5 h-3.5" />
            Пропустить
          </Button>
          <Button
            variant="danger"
            size="sm"
            type="button"
            className="inline-flex items-center gap-1"
            disabled={busy || interrupting}
            onClick={guard(() => controls.pause(stand))}
            title="Прервать текущий тест без исхода — стенд встанет до «Продолжить»"
          >
            <Pause className="w-3.5 h-3.5" />
            Остановить
          </Button>
        </>
      )}
      {showPaused && (
        <Button
          variant="primary"
          size="sm"
          type="button"
          className="inline-flex items-center gap-1"
          disabled={busy}
          onClick={guard(() => controls.resume(stand))}
          title="Вернуть остановленный тест в конец очереди и продолжить работу стенда"
        >
          <Play className="w-3.5 h-3.5" />
          Продолжить
        </Button>
      )}
    </>
  );
}

export function TestingOverview({ runsState }: { runsState: RunsState }) {
  const mockMode = useMockMode();
  const toast = useToast();
  const { confirm } = useConfirm();
  const [concept, setConcept] = useState<ConceptId>("strips");
  const [filter, setFilter] = useState<StandFilter>("all");
  const [queueStandId, setQueueStandId] = useState<number | null>(null);
  const [launchModal, setLaunchModal] = useState<LaunchModal>(null);
  const [logTarget, setLogTarget] = useState<LogTarget | null>(null);
  const [dashboardOpen, setDashboardOpen] = useState(true);
  const [pendingStandId, setPendingStandId] = useState<string | null>(null);

  const liveStandsQuery = useQuery(fetchLiveStands, [], { enabled: !mockMode });
  const queuesQuery = useQuery(fetchStandQueues, [], { enabled: !mockMode, keepPreviousDataOnError: true });
  const osVersionsQuery = useQuery(() => listOsVersions({ limit: 200 }), [], { enabled: !mockMode });
  const testsQuery = useQuery(() => listTestDefinitions({ limit: 500 }), [], { enabled: !mockMode });

  // Очередь меняет не только эта страница: прерывание подтверждает воркер,
  // следующий item подхватывает сам сервис — состояние опрашивается.
  const refetchQueues = queuesQuery.refetch;
  useEffect(() => {
    if (mockMode) return;
    const timer = setInterval(refetchQueues, 5000);
    return () => clearInterval(timer);
  }, [mockMode, refetchQueues]);

  const osNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const version of osVersionsQuery.data?.items ?? []) map.set(version.id, version.name);
    return map;
  }, [osVersionsQuery.data]);

  const rcOptions = useMemo<RcOption[]>(
    () =>
      mockMode
        ? OS_VERSIONS.map((version) => ({ value: version.id, label: version.id, kernels: version.kernels }))
        : (osVersionsQuery.data?.items ?? []).map((version) => ({
            value: version.id,
            label: version.name,
            kernels: version.kernels ?? [],
          })),
    [mockMode, osVersionsQuery.data],
  );

  const launchableTests = useMemo<LaunchableTest[]>(
    () =>
      mockMode
        ? TEST_CATALOG.map((entry) => ({ id: entry.name, label: entry.name, standKey: String(entry.homeStandId) }))
        : (testsQuery.data?.items ?? []).map((test: TestDefinition) => ({
            id: test.id,
            label: `${test.full_name} · ${test.code}`,
            standKey: test.pinned_stand_id,
          })),
    [mockMode, testsQuery.data],
  );

  const stands = useMemo<Stand[]>(
    () =>
      mockMode
        ? STANDS
        : mapLiveStands(liveStandsQuery.data ?? [], queuesQuery.data ?? new Map<string, StandQueue>(), osNameById),
    [mockMode, liveStandsQuery.data, queuesQuery.data, osNameById],
  );

  const filteredStands = useMemo(() => {
    return stands.filter((stand) => {
      if (filter === "testing") return stand.status === "testing";
      if (filter === "busy") return stand.status === "testing" || stand.status === "manual";
      return true;
    });
  }, [filter, stands]);

  const totals = useMemo(
    () => ({
      all: stands.length,
      testing: stands.filter((stand) => stand.status === "testing").length,
      manual: stands.filter((stand) => stand.status === "manual").length,
      busy: stands.filter((stand) => stand.status === "testing" || stand.status === "manual").length,
    }),
    [stands],
  );
  const queueStand = stands.find((stand) => stand.id === queueStandId) ?? null;

  async function runQueueAction(
    standId: string,
    action: () => Promise<unknown>,
    okMessage: string,
    failMessage: string,
  ) {
    if (pendingStandId) return;
    setPendingStandId(standId);
    try {
      await action();
      toast.success(okMessage);
      refetchQueues();
    } catch (error) {
      toast.error(apiErrMsg(error, failMessage));
    } finally {
      setPendingStandId(null);
    }
  }

  // Оба прерывания необратимо убивают процесс на железе, поэтому спрашиваем
  // до похода в API; «Продолжить» ничего не ломает и подтверждения не требует.
  async function runInterrupt(
    stand: Stand,
    action: QueueInterruptAction,
    okMessage: string,
    failMessage: string,
  ) {
    const live = stand.live;
    if (!live?.activeItemId) return;
    const itemId = live.activeItemId;
    if (pendingStandId) return;
    const ok = await confirm(interruptConfirmOptions(action, stand.name));
    if (!ok) return;
    await runQueueAction(
      live.standId,
      () => (action === "skip" ? skipQueueItem(itemId) : pauseQueueItem(itemId)),
      okMessage,
      failMessage,
    );
  }

  const queueControls: QueueControls = {
    pendingStandId,
    skip: (stand) => {
      void runInterrupt(
        stand,
        "skip",
        "Тест пропускается — стенд перейдёт к следующему элементу очереди",
        "Не удалось пропустить тест",
      );
    },
    pause: (stand) => {
      void runInterrupt(
        stand,
        "pause",
        "Тест останавливается — стенд встанет до «Продолжить»",
        "Не удалось остановить тест",
      );
    },
    resume: (stand) => {
      const live = stand.live;
      if (!live?.pausedItemId) return;
      void runQueueAction(
        live.standId,
        () => resumeStandQueue(live.standId),
        "Очередь стенда продолжена — остановленный тест встал в её конец",
        "Не удалось продолжить очередь стенда",
      );
    },
  };

  async function launchTests(
    stand: Stand,
    testIds: string[],
    osVersionId: string,
    kernel: string,
    debugMode: boolean,
  ) {
    const live = stand.live;
    if (!live) {
      setLaunchModal(null);
      return;
    }
    // Backend принимает один тест за запрос — ставим выбранные по одному,
    // чтобы частичный успех остался частичным успехом, а не откатом всего.
    const failed: string[] = [];
    for (const testId of testIds) {
      try {
        await launchQueueItem({
          request_id: crypto.randomUUID(),
          test_id: testId,
          stand_id: live.standId,
          os_version_id: osVersionId,
          kernel,
          debug_mode: debugMode,
        });
      } catch (error) {
        failed.push(apiErrMsg(error, testId));
      }
    }
    if (failed.length) toast.error(`Не поставлено в очередь: ${failed.length} из ${testIds.length}. ${failed[0]}`);
    else toast.success(`Поставлено в очередь тестов: ${testIds.length}`);
    refetchQueues();
    setLaunchModal(null);
  }

  return (
    <div className="grid gap-4">
      {!mockMode && liveStandsQuery.loading && (
        <div role="status" className="text-sm text-dim">Загрузка стендов…</div>
      )}
      {!mockMode && liveStandsQuery.error && (
        <div role="alert" className="alert alert-danger flex items-center gap-3 text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span className="flex-1">{apiErrMsg(liveStandsQuery.error, "Не удалось загрузить стенды")}</span>
          <Button size="sm" onClick={liveStandsQuery.refetch}>Повторить</Button>
        </div>
      )}
      <PoolOverviewPanel
        mockMode={mockMode}
        demoStands={mockMode ? stands : undefined}
        runs={runsState.runs}
        open={dashboardOpen}
        onToggle={() => setDashboardOpen((v) => !v)}
      />

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold">Рабочая зона тестирования</h2>
          <div className="text-sm text-dim mt-1">{stands.length} стендов, отдельная очередь у каждого стенда</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <Button variant="primary" size="sm" type="button" className="inline-flex items-center gap-2" onClick={() => setLaunchModal("test")}>
            <Play className="w-4 h-4" />
            Запустить тест
          </Button>
          <Button size="sm" type="button" className="inline-flex items-center gap-2" onClick={() => setLaunchModal("run")}>
            <ListChecks className="w-4 h-4" />
            Запустить прогон
          </Button>
          <div className="surface border border-token rounded p-1 flex items-center gap-1 flex-wrap">
            {[
              { id: "cards" as const, label: "Карточки", icon: LayoutGrid },
              { id: "strips" as const, label: "Полоски", icon: ListTree },
              { id: "queue" as const, label: "Очереди", icon: BarChart3 },
            ].map((item) => {
              const Icon = item.icon;
              return (
                <Button
                  key={item.id}
                  type="button"
                  onClick={() => setConcept(item.id)}
                  size="sm"
                  variant={concept === item.id ? "primary" : "default"}
                  className="inline-flex items-center gap-2"
                >
                  <Icon className="w-4 h-4" />
                  <span>{item.label}</span>
                </Button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="surface border border-token rounded p-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          {[
            { id: "all" as const, label: "Все", count: totals.all },
            { id: "testing" as const, label: "Идет тестирование", count: totals.testing },
            { id: "busy" as const, label: "Занятые", count: totals.busy },
          ].map((item) => (
            <Button
              key={item.id}
              type="button"
              onClick={() => setFilter(item.id)}
              size="sm"
              variant={filter === item.id ? "primary" : "default"}
              className="inline-flex items-center gap-2"
            >
              <span>{item.label}</span>
              <span className="mono text-[11px] opacity-80">{item.count}</span>
            </Button>
          ))}
        </div>
      </div>

      {concept === "cards" && (
        <CardsConcept
          stands={filteredStands}
          controls={queueControls}
          onOpenQueue={setQueueStandId}
          onOpenLog={(stand, item) => setLogTarget({ stand, item })}
        />
      )}
      {concept === "strips" && (
        <StripsConcept
          stands={filteredStands}
          controls={queueControls}
          onOpenQueue={setQueueStandId}
          onOpenLog={(stand, item) => setLogTarget({ stand, item })}
        />
      )}
      {concept === "queue" && <QueueConcept stands={filteredStands} controls={queueControls} />}

      {queueStand && (
        <QueueModal
          stand={queueStand}
          controls={queueControls}
          onClose={() => setQueueStandId(null)}
          onChanged={refetchQueues}
          onOpenLog={(item) => setLogTarget({ stand: queueStand, item })}
        />
      )}
      {launchModal === "test" && (
        <LaunchTestModal
          stands={stands}
          rcOptions={rcOptions}
          tests={launchableTests}
          live={!mockMode}
          onClose={() => setLaunchModal(null)}
          onSubmit={launchTests}
        />
      )}
      {launchModal === "run" && <LaunchRunModal state={runsState} onClose={() => setLaunchModal(null)} />}
      {logTarget?.item?.itemId ? (
        <AttemptLogModal
          stand={logTarget.stand}
          item={logTarget.item}
          queueItemId={logTarget.item.itemId}
          onClose={() => setLogTarget(null)}
        />
      ) : (
        logTarget && <LogViewerModal stand={logTarget.stand} item={logTarget.item} onClose={() => setLogTarget(null)} />
      )}
    </div>
  );
}

// ── обзор пула (§F плана 2026-09-11) ────────────────────────────────────────

const POOL_STATUS_META: Record<PoolStandStatus, { label: string; icon: typeof Activity; badge: "ok" | "warn" | "danger" | "info" | "neutral" }> = {
  recovering: { label: "Восстанавливается", icon: RotateCw, badge: "warn" },
  unreachable: { label: "Недоступен", icon: CircleDot, badge: "danger" },
  testing: { label: "Тест идёт", icon: Activity, badge: "info" },
  testing_done: { label: "Тестирование завершено", icon: CheckCircle2, badge: "warn" },
  ready: { label: "Готов", icon: CheckCircle2, badge: "ok" },
  no_data: { label: "Нет данных", icon: HelpCircle, badge: "neutral" },
};

const POOL_STATUS_ORDER: PoolStandStatus[] = ["recovering", "unreachable", "testing", "testing_done", "ready", "no_data"];

/** `Stat`-совместимый (только ok/warn/danger/undefined) цвет для карточки статуса стенда. */
const POOL_STATUS_STAT_KIND: Record<PoolStandStatus, "ok" | "warn" | "danger" | undefined> = {
  recovering: "warn",
  unreachable: "danger",
  testing: undefined,
  testing_done: "warn",
  ready: "ok",
  no_data: undefined,
};

/** Обзор пула из demo-стендов `_shared.tsx` — то же, что реальный ответ, для mock-режима. */
function demoPoolOverview(stands: Stand[]): PoolOverviewResponse {
  const toPoolStatus: Record<StandStatus, PoolStandStatus> = {
    testing: "testing", manual: "ready", idle: "ready", offline: "unreachable",
  };
  const counts: Record<PoolStandStatus, number> = { recovering: 0, unreachable: 0, testing: 0, testing_done: 0, ready: 0, no_data: 0 };
  let remaining = 0, running = 0, succeeded = 0, failed = 0;
  const now = new Date().toISOString();
  const standRows = stands.map((s) => {
    const status = toPoolStatus[s.status];
    counts[status] += 1;
    const stats = queueStats(s.queue);
    remaining += stats.pending;
    running += stats.running;
    succeeded += stats.done;
    failed += stats.failed;
    return {
      stand_id: String(s.id), server_id: s.name, status,
      busy_state: s.status, busy_service_name: s.status === "testing" ? "testing_service" : null,
      ping_reachable: s.status !== "offline", ping_checked_at: now,
    };
  });
  return {
    context: "all", test_run_id: null, test_run: null,
    remaining, running, succeeded, failed,
    stands: standRows, stand_status_counts: counts, generated_at: now,
  };
}

const POOL_CONTEXTS: { id: PoolOverviewContext; label: string }[] = [
  { id: "all", label: "Все задания" },
  { id: "run", label: "Выбранный прогон" },
  { id: "standalone", label: "Одиночное тестирование" },
];

/**
 * Обзор пула с нуля (§F плана 2026-09-11) — реальные агрегаты `testing_service`
 * (`GET /pool-overview`), не синтетические CPU/RAM-заглушки прежнего
 * `FleetDashboard`. Три независимых блока: общая очередь (осталось/выполняется),
 * исходы по последней попытке логического теста (успех/провал), и статусы
 * стендов с приоритетом восстановление → недоступен → тест идёт → готов
 * («нет данных» — когда ping не измерялся или устарел, не выдуманный offline).
 *
 * Поллинг раз в 15с держит числа свежими без ручного обновления после
 * старта/завершения/отмены/retry задания; ручная кнопка «Обновить» — для
 * немедленной проверки.
 */
function PoolOverviewPanel({
  mockMode,
  demoStands,
  runs,
  open,
  onToggle,
}: {
  mockMode: boolean;
  demoStands?: Stand[];
  runs: TestRun[];
  open: boolean;
  onToggle: () => void;
}) {
  const [context, setContext] = useState<PoolOverviewContext>("all");
  const [testRunId, setTestRunId] = useState<string>("");

  const overviewQuery = useQuery(
    () => getPoolOverview({ context, test_run_id: context === "run" ? testRunId : undefined }),
    [context, testRunId],
    { enabled: !mockMode && (context !== "run" || !!testRunId), keepPreviousDataOnError: true },
  );

  useEffect(() => {
    if (mockMode || !open) return;
    const timer = setInterval(overviewQuery.refetch, 15000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mockMode, open, context, testRunId]);

  const overview: PoolOverviewResponse | undefined = mockMode
    ? demoPoolOverview(demoStands ?? [])
    : overviewQuery.data;

  const runOptions: DropdownOption[] = runs.map((r) => ({
    value: r.id,
    label: `${r.os_version_id} · ${r.kernel} · ${r.mode ?? "смешанный режим"} · ${r.id.slice(0, 10)}`,
  }));

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <button type="button" onClick={onToggle} className="w-full flex items-center justify-between px-4 py-3 hover-bg">
        <div className="flex items-center gap-2">
          <Gauge className="w-4 h-4 text-accent" />
          <span className="font-semibold">Обзор пула</span>
          <span className="text-xs text-dim">очередь, исходы и статусы стендов — реальные данные testing_service</span>
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-dim" /> : <ChevronRight className="w-4 h-4 text-dim" />}
      </button>
      {open && (
        <div className="border-t border-token p-4 grid gap-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="surface-2 border border-token rounded p-1 flex items-center gap-1 flex-wrap">
              {POOL_CONTEXTS.map((item) => (
                <Button
                  key={item.id}
                  type="button"
                  size="sm"
                  variant={context === item.id ? "primary" : "default"}
                  onClick={() => setContext(item.id)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              {context === "run" && (
                <Dropdown
                  mode="single"
                  label="Прогон"
                  options={runOptions}
                  value={testRunId}
                  onChange={setTestRunId}
                  placeholder="Выберите прогон…"
                />
              )}
              {!mockMode && (
                <>
                  {overview && (
                    <span className="text-xs text-dim">
                      Обновлено {new Date(overview.generated_at).toLocaleTimeString()}
                    </span>
                  )}
                  <Button size="sm" type="button" className="inline-flex items-center gap-2" onClick={overviewQuery.refetch}>
                    <RefreshCcw className="w-3.5 h-3.5" /> Обновить
                  </Button>
                </>
              )}
            </div>
          </div>

          {!mockMode && context === "run" && !testRunId && (
            <div className="text-sm text-dim">Выберите прогон, чтобы увидеть его очередь и исходы.</div>
          )}
          {!mockMode && overviewQuery.error && (
            <div role="alert" className="alert alert-danger flex items-center gap-3 text-sm">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span className="flex-1">{apiErrMsg(overviewQuery.error, "Не удалось загрузить обзор пула")}</span>
              <Button size="sm" onClick={overviewQuery.refetch}>Повторить</Button>
            </div>
          )}

          {overview && context === "run" && overview.test_run && (
            <div className="text-xs text-dim">
              РЦ {overview.test_run.os_version_id} · ядро {overview.test_run.kernel} · режим {overview.test_run.mode ?? "смешанный"} ·
              статус {overview.test_run.status} · id {overview.test_run.id}
            </div>
          )}

          {overview && (
            <>
              <div>
                <div className="text-xs text-dim mb-2">Общая очередь</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <Stat title="Осталось выполнить" value={String(overview.remaining)} icon={ListChecks} kind={overview.remaining > 0 ? "warn" : "ok"} />
                  <Stat title="Выполняются" value={String(overview.running)} icon={Activity} kind={overview.running > 0 ? "warn" : "ok"} />
                  <Stat title="Успешно" value={String(overview.succeeded)} icon={CheckCircle2} kind="ok" />
                  <Stat title="Упало" value={String(overview.failed)} icon={AlertTriangle} kind={overview.failed > 0 ? "danger" : "ok"} />
                </div>
              </div>

              <div>
                <div className="text-xs text-dim mb-2">Серверы пула</div>
                <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
                  {POOL_STATUS_ORDER.map((status) => {
                    const meta = POOL_STATUS_META[status];
                    return (
                      <Stat
                        key={status}
                        title={meta.label}
                        value={String(overview.stand_status_counts[status] ?? 0)}
                        icon={meta.icon}
                        kind={POOL_STATUS_STAT_KIND[status]}
                      />
                    );
                  })}
                </div>
              </div>

            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── концепты списка стендов ─────────────────────────────────────────────────

function CardsConcept({
  stands,
  controls,
  onOpenQueue,
  onOpenLog,
}: {
  stands: Stand[];
  controls: QueueControls;
  onOpenQueue: (standId: number) => void;
  onOpenLog: (stand: Stand, item?: QueueItem) => void;
}) {
  if (!stands.length) return <EmptySearch />;
  return (
    <div className="columns-1 md:columns-2 xl:columns-3 2xl:columns-4 gap-3">
      {stands.map((stand) => (
        <StandCard
          key={stand.id}
          stand={stand}
          controls={controls}
          onOpenQueue={onOpenQueue}
          onOpenLog={onOpenLog}
        />
      ))}
    </div>
  );
}

function StripsConcept({
  stands,
  controls,
  onOpenQueue,
  onOpenLog,
}: {
  stands: Stand[];
  controls: QueueControls;
  onOpenQueue: (standId: number) => void;
  onOpenLog: (stand: Stand, item?: QueueItem) => void;
}) {
  if (!stands.length) return <EmptySearch />;
  return (
    <div className="grid gap-2">
      {stands.map((stand) => {
        const stats = queueStats(stand.queue);
        const meta = STATUS_META[stand.status];
        const Icon = meta.icon;
        const current = currentQueueItem(stand);
        const cpuTotal = stand.metrics.cpuUser + stand.metrics.cpuSystem;
        return (
          <details key={stand.id} className="surface border border-token rounded overflow-hidden group">
            <summary className="grid grid-cols-1 xl:grid-cols-[230px_minmax(190px,1fr)_190px_170px_240px_250px_34px] gap-3 items-center px-4 py-3 cursor-pointer hover-bg list-none">
              <div className="flex items-center gap-3 min-w-0">
                <Badge kind={meta.badge} className="inline-flex items-center gap-1 shrink-0">
                  <Icon className="w-3 h-3" />
                  {meta.label}
                </Badge>
                <div className="min-w-0">
                  <div className="font-semibold truncate">{stand.name}</div>
                  <div className="mono text-xs text-dim truncate">{stand.ip}</div>
                </div>
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{stand.currentTitle}</div>
                <div className="grid grid-cols-2 gap-2 mt-1">
                  <LoadMeter label="CPU" value={cpuTotal} compact />
                  <LoadMeter label="RAM" value={stand.metrics.ram} compact />
                </div>
              </div>
              <div className="mono text-xs text-dim truncate">{stand.kernel}</div>
              <div className="text-xs text-dim truncate">{stand.os}</div>
              <div>
                <div className="text-xs text-dim mb-1 flex gap-3 flex-wrap">
                  <span>всего {stand.queue.length}</span>
                  <span className="text-ok">ok {stats.done}</span>
                  <span className="text-danger">fail {stats.failed}</span>
                  <span className="text-warn">осталось {stats.pending + stats.running + stats.paused}</span>
                </div>
                <QueueBar queue={stand.queue} />
              </div>
              <div className="flex items-center gap-1 flex-wrap">
                <StandQueueActions stand={stand} controls={controls} swallowEvents />
                <Button size="sm"
                  type="button"
                  className="inline-flex items-center gap-1"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onOpenQueue(stand.id);
                  }}
                >
                  <ListChecks className="w-3.5 h-3.5" />
                  Очередь
                </Button>
              </div>
              <span className="text-dim group-open:rotate-180 transition-transform">⌄</span>
            </summary>
            <div className="border-t border-token p-4 grid gap-3">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <InfoBox label="Текущий тест" value={current?.title ?? stand.currentTitle} />
                <InfoBox label="ОС" value={stand.os} />
                <InfoBox label="Ядро" value={stand.kernel} />
              </div>
              <StandMetricsGrid stand={stand} />
              <div className="surface-2 border border-token rounded p-3">
                <div className="text-xs text-dim mb-2">Подробная информация</div>
                <div className="text-sm">{stand.currentMeta}</div>
              </div>
              <StandLogPreview stand={stand} item={current} onOpen={onOpenLog} maxHeight="max-h-44" />
            </div>
          </details>
        );
      })}
    </div>
  );
}

function StandMetricsGrid({ stand }: { stand: Stand }) {
  const m = stand.metrics;
  return (
    <div className="surface-2 border border-token rounded p-3 grid gap-2">
      <div className="text-xs text-dim flex items-center gap-1">
        <Cpu className="w-3.5 h-3.5" /> Детальные метрики
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <LoadMeter label="CPU user" value={m.cpuUser} compact />
        <LoadMeter label="CPU system" value={m.cpuSystem} compact />
        <LoadMeter label="Диск NVMe" value={m.diskNvme} compact />
        <LoadMeter label="Диск SDA" value={m.diskSda} compact />
      </div>
      <div className="flex items-center gap-1.5 text-xs text-dim">
        <Thermometer className="w-3.5 h-3.5" />
        Температура CPU: <span className={`mono ${m.cpuTemp >= 75 ? "text-danger" : m.cpuTemp >= 60 ? "text-warn" : "text-ok"}`}>{stand.status === "offline" ? "—" : `${m.cpuTemp}°C`}</span>
      </div>
    </div>
  );
}

/**
 * Превью лога текущего теста стенда. На живых данных текст лога не тянется
 * прямо в список (это WS-поток на каждый стенд) — превью показывает состояние
 * попытки, а сам журнал открывается в модалке по кнопке. Demo-режим
 * по-прежнему рисует синтетический `demoQueueLog`.
 */
function StandLogPreview({
  stand,
  item,
  onOpen,
  maxHeight,
}: {
  stand: Stand;
  item?: QueueItem;
  onOpen: (stand: Stand, item?: QueueItem) => void;
  maxHeight: string;
}) {
  const live = !!stand.live;
  const unavailable = item?.logStatus === "rotated" || item?.logStatus === "missing";
  return (
    <div className="surface-2 border border-token rounded overflow-hidden">
      <div className="border-b border-token px-3 py-2 text-xs text-dim">Лог текущего теста</div>
      {live ? (
        <div className="p-3 text-xs grid gap-1">
          {item ? (
            <>
              <div className="mono truncate">{item.title}</div>
              <div className="text-dim">
                {QUEUE_TEXT[item.state]} · {item.meta}
              </div>
              {unavailable && (
                <div className="text-warn">
                  {item.logStatus === "rotated"
                    ? "Лог ротирован по сроку хранения — результат сохранён."
                    : "Текст лога этой попытки отсутствует."}
                </div>
              )}
            </>
          ) : (
            <div className="text-dim">Нет активного теста — журнал появится после постановки в очередь.</div>
          )}
        </div>
      ) : (
        <pre className={`mono text-xs p-3 overflow-auto ${maxHeight} whitespace-pre-wrap`}>{demoQueueLog(stand, item)}</pre>
      )}
      <div className="border-t border-token p-2 flex justify-end">
        <Button size="sm"
          type="button"
          className="inline-flex items-center gap-1"
          disabled={live && (!item || unavailable)}
          onClick={() => onOpen(stand, item)}
        >
          <ExternalLink className="w-3.5 h-3.5" />
          Открыть журнал целиком
        </Button>
      </div>
    </div>
  );
}

/** Журнал реальной попытки — тот же просмотрщик, что в разделе логов. */
function AttemptLogModal({
  stand,
  item,
  queueItemId,
  onClose,
}: {
  stand: Stand;
  item: QueueItem;
  queueItemId: string;
  onClose: () => void;
}) {
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`Журнал · ${item.title}`}
      subtitle={`${stand.name} · ${stand.ip}`}
      width="lg"
    >
      <div className="flex flex-col min-h-[45vh] max-h-[70vh]">
        <AttemptLogViewer key={queueItemId} queueItemId={queueItemId} state={item.rawState ?? ""} />
      </div>
    </Modal>
  );
}

function LaunchTestModal({
  stands,
  rcOptions,
  tests,
  live,
  onClose,
  onSubmit,
}: {
  stands: Stand[];
  rcOptions: RcOption[];
  tests: LaunchableTest[];
  /** Живой режим — отправляем реальные `POST /queue-items`. */
  live: boolean;
  onClose: () => void;
  onSubmit: (
    stand: Stand,
    testIds: string[],
    osVersionId: string,
    kernel: string,
    debugMode: boolean,
  ) => Promise<void>;
}) {
  const [debugMode, setDebugMode] = useState(false);
  const [busy, setBusy] = useState(false);
  // Штатно доступны только физические стенды — виртуальные существуют
  // исключительно как цель для debug-режима (отладочный запуск теста на ВМ).
  const pickableStands = debugMode ? stands : stands.filter((item) => item.kind !== "virtual");
  const [standId, setStandId] = useState(pickableStands[0]?.id ?? 0);
  const stand = pickableStands.find((item) => item.id === standId) ?? pickableStands[0];
  const available = stand ? testsForStand(stand, tests, debugMode) : [];
  const [selectedTests, setSelectedTests] = useState<string[]>(() => available.slice(0, 2).map((item) => item.id));
  const [rcId, setRcId] = useState(rcOptions[0]?.value ?? "");
  const kernels = rcOptions.find((option) => option.value === rcId)?.kernels ?? [];
  const [kernel, setKernel] = useState(kernels[0] ?? "");

  const switchStand = (nextStandId: number) => {
    const nextStand = pickableStands.find((item) => item.id === nextStandId) ?? pickableStands[0];
    setStandId(nextStandId);
    setSelectedTests(nextStand ? testsForStand(nextStand, tests, debugMode).slice(0, 2).map((item) => item.id) : []);
  };
  const switchRc = (nextRc: string) => {
    setRcId(nextRc);
    setKernel(rcOptions.find((option) => option.value === nextRc)?.kernels[0] ?? "");
  };
  const toggleDebugMode = (enabled: boolean) => {
    setDebugMode(enabled);
    // выключение debug-режима могло сделать выбранный (виртуальный) стенд
    // недоступным — падаем на первый штатный стенд
    const nextStands = enabled ? stands : stands.filter((item) => item.kind !== "virtual");
    const nextStand = nextStands.find((item) => item.id === standId) ?? nextStands[0];
    if (nextStand) {
      setStandId(nextStand.id);
      setSelectedTests(testsForStand(nextStand, tests, enabled).slice(0, 2).map((item) => item.id));
    }
  };
  const toggleTest = (testId: string) => {
    setSelectedTests((current) =>
      current.includes(testId)
        ? current.filter((item) => item !== testId)
        : [...current, testId],
    );
  };
  const submit = async () => {
    if (!stand || busy) return;
    setBusy(true);
    try {
      await onSubmit(stand, selectedTests, rcId, kernel, debugMode);
    } finally {
      setBusy(false);
    }
  };

  const incomplete = live && (!rcId || !kernel);

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
      title="Запустить тест"
      subtitle="Добавление выбранных тестов в очередь стенда"
      width="md"
      footer={
        <>
          <Button type="button" onClick={onClose} disabled={busy}>Отмена</Button>
          <Button
            variant="primary"
            type="button"
            disabled={busy || !selectedTests.length || !stand || incomplete}
            onClick={submit}
            title={!selectedTests.length ? "Выберите хотя бы один тест" : incomplete ? "Выберите РЦ и ядро" : undefined}
          >
            {busy ? "Ставим в очередь…" : "Добавить в очередь"}
          </Button>
        </>
      }
    >
        <div className="grid gap-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <label className="grid gap-1">
              <span className="text-xs text-dim">Стенд</span>
              <Dropdown
                mode="single"
                options={pickableStands.map((item) => ({
                  value: String(item.id),
                  label: item.kind === "virtual" ? `${item.name} (ВМ)` : item.name,
                }))}
                value={String(standId)}
                onChange={(v) => switchStand(Number(v))}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-xs text-dim">RC</span>
              <Dropdown
                mode="single"
                options={rcOptions.map((option) => ({ value: option.value, label: option.label }))}
                value={rcId}
                onChange={switchRc}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-xs text-dim">Ядро</span>
              <Dropdown
                mode="single"
                options={kernels.map((value) => ({ value, label: value }))}
                value={kernel}
                onChange={setKernel}
                disabled={!kernels.length}
              />
            </label>
          </div>

          <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
            <Checkbox
              checked={debugMode}
              onChange={(event) => toggleDebugMode(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="text-sm font-medium block">Debug режим</span>
              <span className="text-xs text-dim">
                Снимает привязку теста к своему стенду — можно запустить любой тест на любом стенде,
                включая виртуальные (ВМ). Только для разового отладочного запуска — в прогоне привязка
                тест → стенд всегда соблюдается
              </span>
            </span>
          </label>

          <div className="surface-2 border border-token rounded p-3">
            <div className="text-xs text-dim mb-2">
              {debugMode ? "Все тесты (debug режим)" : "Тесты, закреплённые за стендом"}
            </div>
            {available.length ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {available.map((test) => (
                  <label key={test.id} className="surface border border-token rounded p-2 flex items-center gap-2 text-sm">
                    <Checkbox checked={selectedTests.includes(test.id)} onChange={() => toggleTest(test.id)} />
                    <span>{test.label}</span>
                  </label>
                ))}
              </div>
            ) : (
              <div className="text-xs text-dim">
                За этим стендом не закреплён ни один тест. Включите debug режим, чтобы выбрать тест из общего каталога.
              </div>
            )}
          </div>

          <div className="text-xs text-dim">
            Каждый выбранный тест ставится в очередь стенда отдельным запросом — частичный отказ не отменяет уже
            поставленные.
          </div>
        </div>
    </Modal>
  );
}

/**
 * Очередь стенда: реальный упорядоченный список элементов и управление
 * активным/остановленным из него же.
 *
 * Переупорядочивание перетаскиванием и точечное удаление будущего элемента
 * сознательно не реализованы — под них нет эндпоинтов на стороне
 * `testing_service`, а подделывать их локальным состоянием (как было в
 * demo-макете) значит показывать пользователю то, чего не произошло.
 */
function QueueModal({
  stand,
  controls,
  onClose,
  onChanged,
  onOpenLog,
}: {
  stand: Stand;
  controls: QueueControls;
  onClose: () => void;
  onChanged: () => void;
  onOpenLog: (item: QueueItem) => void;
}) {
  const live = stand.live;
  const standId = live?.standId;
  const toast = useToast();
  const [retryingId, setRetryingId] = useState<string | null>(null);
  // Окно запрашивается от новых к старым: активный и остановленный элементы
  // всегда свежие, а при длинной истории стенда сортировка от старых их бы
  // просто не захватила. Порядок очереди на экране — обратный, от старых.
  const itemsQuery = useQuery(
    () =>
      standId
        ? listQueueItems({ kind: "all", stand_id: standId, order: "desc", limit: 200 })
        : Promise.resolve(null),
    [standId],
    { enabled: !!standId },
  );

  const rows: QueueItem[] = live
    ? (itemsQuery.data?.items ?? []).slice().reverse().map(toQueueItem)
    : stand.queue;

  async function retry(itemId: string) {
    if (retryingId) return;
    setRetryingId(itemId);
    try {
      await retryQueueItem(itemId, crypto.randomUUID());
      toast.success("Создана новая попытка теста");
      itemsQuery.refetch();
      onChanged();
    } catch (error) {
      toast.error(apiErrMsg(error, "Не удалось повторить тест"));
    } finally {
      setRetryingId(null);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`Очередь ${stand.name}`}
      subtitle={stand.ip}
      width="lg"
    >
        {live && (
          <div className="surface-2 border border-token rounded p-3 mb-3 flex items-center justify-between gap-3 flex-wrap">
            <div className="text-xs text-dim">
              Порядок очереди задаёт сам сервис — перетаскивание и удаление отдельного элемента из середины пока не
              поддерживаются.
            </div>
            <Button size="sm" type="button" className="inline-flex items-center gap-1" onClick={itemsQuery.refetch}>
              <RefreshCcw className="w-3.5 h-3.5" />
              Обновить
            </Button>
          </div>
        )}
        {live && itemsQuery.loading && <div role="status" className="text-sm text-dim mb-3">Загрузка очереди…</div>}
        {live && itemsQuery.error && (
          <div role="alert" className="alert alert-danger flex items-center gap-3 text-sm mb-3">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span className="flex-1">{apiErrMsg(itemsQuery.error, "Не удалось загрузить очередь стенда")}</span>
            <Button size="sm" onClick={itemsQuery.refetch}>Повторить</Button>
          </div>
        )}
        <div>
          {rows.length ? (
            <div className="grid gap-2">
              {rows.map((item, index) => (
                <div
                  key={item.itemId ?? `${item.title}-${index}`}
                  className="surface-2 border border-token rounded p-3 grid grid-cols-1 lg:grid-cols-[42px_1fr_auto] gap-3 items-center"
                >
                  <div className="mono text-xs text-dim flex items-center gap-2">
                    <ListTree className="w-3.5 h-3.5" />
                    {index + 1}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <div className="text-sm font-medium truncate">{item.title}</div>
                      <Badge kind={queueBadge(item.state)}>{QUEUE_TEXT[item.state]}</Badge>
                      {item.knownIssue && <KnownIssueBadge issue={item.knownIssue} />}
                    </div>
                    <div className="text-xs text-dim mt-1">{item.meta}</div>
                    {item.itemId && <div className="mono text-[11px] text-dim mt-1 truncate">{item.itemId}</div>}
                    {item.log && <div className="mono text-[11px] text-accent mt-1 truncate">{item.log}</div>}
                  </div>
                  <div className="flex items-center gap-1 flex-wrap justify-start lg:justify-end">
                    {(item.log || (item.itemId && item.logStatus !== "missing" && item.logStatus !== "rotated")) && (
                      <Button size="sm" type="button" className="inline-flex items-center gap-1" onClick={() => onOpenLog(item)}>
                        <ExternalLink className="w-4 h-4" />
                        Лог
                      </Button>
                    )}
                    {item.itemId && item.itemId === live?.activeItemId && (
                      <StandQueueActions stand={stand} controls={controls} scope="active" />
                    )}
                    {item.itemId && item.itemId === live?.pausedItemId && (
                      <StandQueueActions stand={stand} controls={controls} scope="paused" />
                    )}
                    {item.itemId && item.canRetry && (
                      <Button
                        size="sm"
                        type="button"
                        className="inline-flex items-center gap-1"
                        disabled={retryingId !== null}
                        onClick={() => retry(item.itemId as string)}
                      >
                        <RefreshCcw className="w-4 h-4" />
                        Ретрай
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptySearch text="Очередь пуста" />
          )}
        </div>
    </Modal>
  );
}

function QueueConcept({ stands, controls }: { stands: Stand[]; controls: QueueControls }) {
  if (!stands.length) return <EmptySearch />;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
      {stands.map((stand) => (
        <div key={stand.id} className="surface border border-token rounded p-4">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div className="min-w-0">
              <div className="font-semibold truncate">{stand.name}</div>
              <div className="mono text-xs text-dim truncate">{stand.ip}</div>
            </div>
            <StatusBadge status={stand.status} />
          </div>
          <QueueSummary stand={stand} />
          <div className="mt-3">
            <QueueList queue={stand.queue} />
          </div>
          <div className="mt-3 flex items-center gap-1 flex-wrap">
            <StandQueueActions stand={stand} controls={controls} />
          </div>
        </div>
      ))}
    </div>
  );
}

function StandCard({
  stand,
  controls,
  onOpenQueue,
  onOpenLog,
}: {
  stand: Stand;
  controls: QueueControls;
  onOpenQueue: (standId: number) => void;
  onOpenLog: (stand: Stand, item?: QueueItem) => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const topColor =
    stand.status === "testing"
      ? "bg-[var(--status-testing)]"
      : stand.status === "idle"
        ? "bg-[var(--status-idle)]"
        : stand.status === "manual"
          ? "bg-[var(--status-acs)]"
          : "bg-[var(--danger)]";
  const current = currentQueueItem(stand);
  const cpuTotal = stand.metrics.cpuUser + stand.metrics.cpuSystem;
  return (
    <article className="surface border border-token rounded overflow-hidden break-inside-avoid mb-3 w-full inline-block">
      <div className={`h-1 ${topColor}`} />
      <div className="p-4">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Server className="w-4 h-4 text-dim shrink-0" />
              <div className="font-semibold truncate">{stand.name}</div>
            </div>
            <div className="mono text-xs text-dim mt-1">{stand.ip}</div>
          </div>
          <StatusBadge status={stand.status} />
        </div>

        <div className="min-h-[52px] mb-3">
          <div className="text-sm font-medium truncate">{stand.currentTitle}</div>
          <div className="text-xs text-dim mt-1 line-clamp-2">{stand.currentMeta}</div>
        </div>

        <div className="grid gap-1.5 mb-3 text-xs">
          <MetaRow label="ОС" value={stand.os} />
          <MetaRow label="Ядро" value={stand.kernel} />
        </div>

        <div className="grid grid-cols-2 gap-2 mb-3">
          <LoadMeter label="CPU" value={cpuTotal} />
          <LoadMeter label="RAM" value={stand.metrics.ram} />
        </div>

        <QueueSummary stand={stand} />

        <div className="grid grid-cols-2 gap-1.5 mt-3">
          <Button
            type="button"
            size="sm"
            variant={detailsOpen ? "primary" : "default"}
            className="inline-flex items-center justify-center gap-1 px-1.5"
            onClick={() => setDetailsOpen((open) => !open)}
          >
            Детали
          </Button>
          <Button size="sm"
            type="button"
            className="inline-flex items-center justify-center gap-1 px-1.5"
            onClick={() => onOpenQueue(stand.id)}
          >
            <ListChecks className="w-4 h-4 shrink-0" />
            Очередь
          </Button>
        </div>
        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
          <StandQueueActions stand={stand} controls={controls} />
        </div>

        {detailsOpen && (
          <div className="mt-3 grid gap-2">
            <InfoBox label="Текущий тест" value={current?.title ?? stand.currentTitle} />
            <StandMetricsGrid stand={stand} />
            <StandLogPreview stand={stand} item={current} onOpen={onOpenLog} maxHeight="max-h-36" />
          </div>
        )}
      </div>
    </article>
  );
}

function QueueSummary({ stand }: { stand: Stand }) {
  const stats = queueStats(stand.queue);
  const next = stand.queue.find((item) => item.state === "pending");
  return (
    <div className="surface-2 border border-token rounded p-3">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="text-xs font-semibold">Очередь стенда</div>
        <div className="text-xs text-dim">{stand.queue.length} тестов</div>
      </div>
      <QueueBar queue={stand.queue} />
      <div className="grid grid-cols-3 gap-2 mt-3 text-xs">
        <Counter label="Выполнено" value={stats.done} className="text-ok" />
        <Counter label="Провалено" value={stats.failed} className="text-danger" />
        <Counter label="Осталось" value={stats.pending + stats.running + stats.paused} className="text-warn" />
      </div>
      <div className="text-xs text-dim mt-3">
        {next ? (
          <>
            Следующий тест: <span className="text-accent">{next.title}</span>
          </>
        ) : (
          "Следующего теста нет"
        )}
      </div>
    </div>
  );
}

function QueueBar({ queue }: { queue: QueueItem[] }) {
  const stats = queueStats(queue);
  const total = Math.max(queue.length, 1);
  return (
    <div className="h-1.5 rounded-full overflow-hidden surface border border-token flex">
      <span className="bg-[var(--ok)]" style={{ width: `${(stats.done / total) * 100}%` }} />
      <span className="bg-[var(--danger)]" style={{ width: `${(stats.failed / total) * 100}%` }} />
      <span className="bg-[var(--accent)]" style={{ width: `${(stats.running / total) * 100}%` }} />
      <span className="bg-[var(--warn)]" style={{ width: `${((stats.pending + stats.paused + stats.skipped) / total) * 100}%` }} />
    </div>
  );
}

function QueueList({ queue }: { queue: QueueItem[] }) {
  if (!queue.length) {
    return (
      <div className="surface-2 border border-token rounded p-3 text-sm">
        <div className="font-medium">Очередь пуста</div>
        <div className="text-xs text-dim mt-1">Для свободного стенда можно поставить новый набор тестов.</div>
      </div>
    );
  }
  return (
    <div className="grid gap-2">
      {queue.map((item, index) => (
        <div key={item.itemId ?? `${item.title}-${index}`} className="surface-2 border border-token rounded p-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <div className="text-sm font-medium truncate">{item.title}</div>
              {item.knownIssue && <KnownIssueBadge issue={item.knownIssue} />}
            </div>
            <div className="text-xs text-dim mt-1">{item.meta}</div>
            {item.log && <div className="mono text-[11px] text-accent mt-1 truncate">{item.log}</div>}
          </div>
          <Badge kind={queueBadge(item.state)} className="shrink-0">{QUEUE_TEXT[item.state]}</Badge>
        </div>
      ))}
    </div>
  );
}

/**
 * Каждый тест закреплён за одним конкретным стендом — в штатном режиме
 * запустить его можно только там. Debug режим (см. `LaunchTestModal`) снимает
 * это ограничение для разового отладочного запуска, но никогда не
 * используется в прогоне (см. `runs.tsx`).
 *
 * В живом режиме привязка приходит из каталога (`TestDefinition.pinned_stand_id`),
 * в demo-режиме — из локального `TEST_CATALOG` по числовому id стенда.
 */
interface TestCatalogEntry {
  name: string;
  homeStandId: number;
}

const TEST_CATALOG: TestCatalogEntry[] = [
  { name: "UnixBench 5.1.3", homeStandId: 1 },
  { name: "sysbench 1.0.20 / cpu", homeStandId: 9 },
  { name: "sysbench 1.0.20 / memory", homeStandId: 9 },
  { name: "fio 3.38 / randrw", homeStandId: 11 },
  { name: "OpenSSL speed", homeStandId: 15 },
  { name: "PostgreSQL TPC-C", homeStandId: 5 },
  { name: "Linpack Xtreme", homeStandId: 5 },
  { name: "PARSEC 3.0 / streamcluster", homeStandId: 3 },
  { name: "7-Zip 24.08", homeStandId: 13 },
  { name: "iperf3 / network", homeStandId: 6 },
  { name: "GUI smoke", homeStandId: 2 },
];

/**
 * Тесты, доступные для запуска на стенде: в штатном режиме — только
 * закреплённые за ним, в debug режиме — весь каталог целиком, независимо от
 * привязки и от того, физический это стенд или ВМ.
 */
function testsForStand(stand: Stand, tests: LaunchableTest[], debugMode = false): LaunchableTest[] {
  if (debugMode) return tests;
  const key = standKey(stand);
  return tests.filter((test) => test.standKey === key);
}
