/**
 * «Рабочая зона» тестирования — список стендов пула + агрегированный
 * дашборд + панель prepare/testenv. Три визуальных концепта одного и того
 * же списка стендов (карточки/полоски/очереди) сохранены как есть — это
 * материал для согласования с руководителем, какой вид удобнее для
 * повседневной работы; окончательный выбор владелец сделает позже.
 *
 * Источник стендов — `testing_service` (`listTestStands`/`getTestStand`,
 * живая карточка сервера приходит вложенной в ответ `getTestStand`). Пул
 * ещё не отдаёт отдельную телеметрию загрузки (CPU/RAM/температура) и
 * полную историю очереди на стенд — это будущие волны; до появления
 * реального эндпоинта нагрузка на дашборде — детерминированная заглушка
 * (см. `placeholderMetrics`), а очередь строится из единственного активного
 * элемента (`getCurrentQueueItem`), а не полной истории. В mock-режиме
 * (`VITE_USE_MOCK_AUTH=true`) страница по-прежнему работает на demo-данных
 * `_shared.tsx`, как и раньше.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Cpu,
  ExternalLink,
  Gauge,
  HardDrive,
  LayoutGrid,
  ListChecks,
  ListTree,
  MemoryStick,
  Play,
  RefreshCcw,
  Server,
  Square,
  Thermometer,
  Trash2,
} from "lucide-react";
import { LaunchRunModal, type RunsState } from "./runs";
import { Dropdown } from "@/components/ui/Dropdown";
import {
  Counter,
  EmptySearch,
  InfoBox,
  KnownIssueBadge,
  LoadMeter,
  LogViewerModal,
  MetaRow,
  OS_VERSION_IDS as RC_IDS,
  Sparkline,
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
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import {
  getCurrentQueueItem,
  getTestStand,
  listTestStands,
} from "@/api/testing/testStands";
import type {
  QueueItemSummary,
  TestStand,
} from "@/api/testing/types";
import { listOsVersions } from "@/api/server/osVersions";
import { getHostDiskUsage } from "@/api/server/misc";
import type { HostDiskUsageResponse } from "@/api/server/types";

type ConceptId = "cards" | "strips" | "queue";
type LaunchModal = "test" | "run" | null;
type StandFilter = "all" | "testing" | "busy";

interface LogTarget {
  stand: Stand;
  item?: QueueItem;
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
  if (state === "queued") return "pending";
  return "running"; // preparing | ready | running
}

interface LiveStandData {
  stand: TestStand;
  current: QueueItemSummary | null;
}

/** Загружает список стендов + живую карточку сервера + текущий элемент очереди на каждый. */
async function fetchLiveStands(): Promise<LiveStandData[]> {
  const list = await listTestStands({ limit: 500 });
  const detailed = await Promise.all(list.items.map((item) => getTestStand(item.id)));
  const current = await Promise.all(
    detailed.map((stand) => getCurrentQueueItem(stand.id).catch(() => null)),
  );
  return detailed.map((stand, index) => ({ stand, current: current[index] }));
}

/** Строит `Stand[]` (совместимый с demo-типом из `_shared.tsx`) из живых данных testing_service. */
function mapLiveStands(data: LiveStandData[], osNameById: Map<string, string>): Stand[] {
  return data.map(({ stand, current }, index) => {
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
    const currentTitle = current
      ? `тест ${current.test_id.slice(0, 8)}`
      : status === "manual"
        ? server?.busy_note || "ручная работа"
        : "Нет активной работы";
    const currentMeta = current
      ? `${current.state} · занято testing_service`
      : status === "offline"
        ? "стенд недоступен"
        : status === "manual"
          ? "занят вне testing_service"
          : "стенд свободен";
    const queue: QueueItem[] = current
      ? [{ title: currentTitle, state: mapQueueItemState(current.state), meta: currentMeta }]
      : [];
    return {
      id: index + 1,
      name,
      ip,
      status,
      os,
      kernel: "—",
      currentTitle,
      currentMeta,
      metrics: placeholderMetrics(hashSeed(stand.id), status),
      queue,
    };
  });
}

const STORAGE_LABELS: Record<string, string> = {
  "/": "Системный диск",
  "/srv/ftp": "FTP-хранилище",
  "/home/partimag": "Partimag (снимки ACS)",
};

/** Хранилище хоста платформы (`getHostDiskUsage`) в форме, ожидаемой виджетом «Хранилище пула». */
function storageStatsFromDisk(data: HostDiskUsageResponse | null): { label: string; usedGb: number; totalGb: number }[] {
  return (data?.paths ?? [])
    .filter((path) => path.available && path.used_gb != null && path.total_gb != null)
    .map((path) => ({
      label: STORAGE_LABELS[path.path] ?? path.path,
      usedGb: Math.round(path.used_gb as number),
      totalGb: Math.round(path.total_gb as number),
    }));
}

export function TestingOverview({ runsState }: { runsState: RunsState }) {
  const mockMode = useMockMode();
  const [concept, setConcept] = useState<ConceptId>("cards");
  const [filter, setFilter] = useState<StandFilter>("all");
  const [stands, setStands] = useState<Stand[]>(() => (mockMode ? STANDS : []));
  const [queueStandId, setQueueStandId] = useState<number | null>(null);
  const [launchModal, setLaunchModal] = useState<LaunchModal>(null);
  const [logTarget, setLogTarget] = useState<LogTarget | null>(null);
  const [dashboardOpen, setDashboardOpen] = useState(true);

  const liveStandsQuery = useQuery(fetchLiveStands, [], { enabled: !mockMode });
  const osVersionsQuery = useQuery(() => listOsVersions({ limit: 200 }), [], { enabled: !mockMode });
  const hostDiskQuery = useQuery(() => getHostDiskUsage(), [], { enabled: !mockMode });

  const osNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const version of osVersionsQuery.data?.items ?? []) map.set(version.id, version.name);
    return map;
  }, [osVersionsQuery.data]);
  const rcOptions = mockMode ? RC_IDS : (osVersionsQuery.data?.items.map((v) => v.name) ?? []);
  // Число прогонов — общее состояние с вкладкой «Прогоны» (`useRunsState`,
  // всегда живое, без mock-ветки — см. её докстринг), переиспользуем как есть.
  const totalRuns = runsState.total;
  const storageStats = mockMode ? STORAGE_STATS : storageStatsFromDisk(hostDiskQuery.data ?? null);

  // Живые стенды подгружаются один раз при первом ответе — дальше страница
  // управляет ими локально (та же модель, что и demo-режим), чтобы не сбивать
  // локальные изменения очереди случайным рефетчем.
  const [liveLoaded, setLiveLoaded] = useState(false);
  useEffect(() => {
    if (mockMode || liveLoaded || !liveStandsQuery.data) return;
    setStands(mapLiveStands(liveStandsQuery.data, osNameById));
    setLiveLoaded(true);
  }, [mockMode, liveLoaded, liveStandsQuery.data, osNameById]);

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

  const updateQueue = (standId: number, nextQueue: QueueItem[]) => {
    setStands((current) =>
      current.map((stand) =>
        stand.id === standId ? { ...stand, queue: nextQueue } : stand,
      ),
    );
  };
  const setStandTesting = (standId: number, testing: boolean) => {
    setStands((current) =>
      current.map((stand) =>
        stand.id === standId
          ? {
              ...stand,
              status: testing ? "testing" : "idle",
              currentTitle: testing
                ? currentQueueItem(stand)?.title ?? stand.currentTitle
                : "Нет активной работы",
              currentMeta: testing ? "запущено из очереди · demo" : "стенд свободен",
            }
          : stand,
      ),
    );
  };
  const addTestsToQueue = (standId: number, tests: string[], prepareEnv: boolean, debugMode: boolean) => {
    setStands((current) =>
      current.map((stand) => {
        if (stand.id !== standId) return stand;
        const prepItem: QueueItem[] = prepareEnv
          ? [{ title: "Подготовка окружения", state: "pending", meta: "testenv prepare перед запуском выбранных тестов" }]
          : [];
        const testItems = tests.map<QueueItem>((title) => ({
          title,
          state: "pending",
          meta: debugMode ? "запустить тест · debug mode" : "запустить тест",
        }));
        const added = [...prepItem, ...testItems];
        return {
          ...stand,
          queue: [...stand.queue, ...added],
          status: "testing",
          currentTitle: added[0]?.title ?? stand.currentTitle,
          currentMeta: "добавлено в очередь запуска · demo",
        };
      }),
    );
  };

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
      <FleetDashboard
        stands={stands}
        totalRuns={totalRuns}
        storageStats={storageStats}
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
          onOpenQueue={setQueueStandId}
          onSetTesting={setStandTesting}
          onOpenLog={(stand, item) => setLogTarget({ stand, item })}
        />
      )}
      {concept === "strips" && (
        <StripsConcept
          stands={filteredStands}
          onOpenQueue={setQueueStandId}
          onSetTesting={setStandTesting}
          onOpenLog={(stand, item) => setLogTarget({ stand, item })}
        />
      )}
      {concept === "queue" && <QueueConcept stands={filteredStands} />}

      {queueStand && (
        <QueueModal
          stand={queueStand}
          onClose={() => setQueueStandId(null)}
          onChange={(nextQueue) => updateQueue(queueStand.id, nextQueue)}
          onOpenLog={(item) => setLogTarget({ stand: queueStand, item })}
        />
      )}
      {launchModal === "test" && (
        <LaunchTestModal
          stands={stands}
          rcOptions={rcOptions}
          onClose={() => setLaunchModal(null)}
          onSubmit={(standId, tests, prepareEnv, debugMode) => {
            addTestsToQueue(standId, tests, prepareEnv, debugMode);
            setLaunchModal(null);
          }}
        />
      )}
      {launchModal === "run" && <LaunchRunModal state={runsState} onClose={() => setLaunchModal(null)} />}
      {logTarget && (
        <LogViewerModal stand={logTarget.stand} item={logTarget.item} onClose={() => setLogTarget(null)} />
      )}
    </div>
  );
}

// ── дашборд пула ────────────────────────────────────────────────────────────

const STORAGE_STATS = [
  { label: "Системный диск", usedGb: 640, totalGb: 960 },
  { label: "FTP-хранилище", usedGb: 3800, totalGb: 8000 },
  { label: "Partimag (снимки ACS)", usedGb: 5200, totalGb: 6000 },
];

function FleetDashboard({
  stands,
  totalRuns,
  storageStats,
  open,
  onToggle,
}: {
  stands: Stand[];
  totalRuns: number;
  storageStats: { label: string; usedGb: number; totalGb: number }[];
  open: boolean;
  onToggle: () => void;
}) {
  const online = stands.filter((s) => s.status !== "offline");
  const avgCpu = Math.round(
    online.reduce((sum, s) => sum + s.metrics.cpuUser + s.metrics.cpuSystem, 0) / (online.length || 1),
  );
  const avgRam = Math.round(online.reduce((sum, s) => sum + s.metrics.ram, 0) / (online.length || 1));
  const failed24h = stands.reduce((sum, s) => sum + s.queue.filter((q) => q.state === "failed").length, 0);
  const busyStands = stands.filter((s) => s.status === "testing").length;
  const offlineStands = stands.filter((s) => s.status === "offline").length;

  return (
    <div className="surface border border-token rounded overflow-hidden">
      <button type="button" onClick={onToggle} className="w-full flex items-center justify-between px-4 py-3 hover-bg">
        <div className="flex items-center gap-2">
          <Gauge className="w-4 h-4 text-accent" />
          <span className="font-semibold">Обзор пула</span>
          <span className="text-xs text-dim">агрегированные показатели по всем стендам</span>
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-dim" /> : <ChevronRight className="w-4 h-4 text-dim" />}
      </button>
      {open && (
        <div className="border-t border-token p-4 grid gap-4">
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
            <Stat title="Средний CPU" value={`${avgCpu}%`} icon={Cpu} kind={avgCpu >= 70 ? "warn" : "ok"} />
            <Stat title="Средний RAM" value={`${avgRam}%`} icon={MemoryStick} kind={avgRam >= 70 ? "warn" : "ok"} />
            <Stat title="Упало за 24ч" value={String(failed24h)} icon={AlertTriangle} kind={failed24h > 0 ? "danger" : "ok"} />
            <Stat title="Всего прогонов" value={String(totalRuns)} icon={ListChecks} />
            <Stat title="В тесте" value={String(busyStands)} icon={Activity} />
            <Stat title="Недоступно" value={String(offlineStands)} icon={CircleDot} kind={offlineStands > 0 ? "danger" : "ok"} />
          </div>

          <div>
            <div className="text-xs text-dim mb-2 flex items-center gap-1">
              <HardDrive className="w-3.5 h-3.5" /> Хранилище пула
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {storageStats.map((s) => (
                <div key={s.label} className="surface-2 border border-token rounded p-3">
                  <div className="text-xs text-dim mb-1.5">{s.label}</div>
                  <div className="progress-wide mb-1.5">
                    <span style={{ width: `${Math.round((s.usedGb / s.totalGb) * 100)}%` }} />
                  </div>
                  <div className="text-xs mono">{s.usedGb} / {s.totalGb} GB</div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="text-xs text-dim mb-2">CPU по стендам, последние замеры</div>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-2">
              {online.map((s) => (
                <div key={s.id} className="surface-2 border border-token rounded p-2">
                  <div className="flex items-center justify-between text-xs mb-1 gap-2">
                    <span className="truncate">{s.name}</span>
                    <span className="mono text-dim shrink-0">{s.metrics.cpuUser + s.metrics.cpuSystem}%</span>
                  </div>
                  <Sparkline values={s.metrics.history} className="w-full h-5 text-accent" />
                </div>
              ))}
            </div>
          </div>

          <div className="surface-2 border border-token rounded overflow-hidden">
            <table className="mini">
              <thead>
                <tr>
                  <th>Стенд</th>
                  <th>Статус</th>
                  <th>CPU</th>
                  <th>RAM</th>
                  <th>Темп.</th>
                  <th>Очередь</th>
                </tr>
              </thead>
              <tbody>
                {stands.map((s) => {
                  const stats = queueStats(s.queue);
                  return (
                    <tr key={s.id}>
                      <td className="mono">{s.name}</td>
                      <td><StatusBadge status={s.status} /></td>
                      <td className="mono">{s.metrics.cpuUser + s.metrics.cpuSystem}%</td>
                      <td className="mono">{s.metrics.ram}%</td>
                      <td className="mono">{s.status === "offline" ? "—" : `${s.metrics.cpuTemp}°C`}</td>
                      <td className="text-xs text-dim">
                        ok {stats.done} · fail {stats.failed} · в очереди {stats.pending + stats.running}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── концепты списка стендов ─────────────────────────────────────────────────

function CardsConcept({
  stands,
  onOpenQueue,
  onSetTesting,
  onOpenLog,
}: {
  stands: Stand[];
  onOpenQueue: (standId: number) => void;
  onSetTesting: (standId: number, testing: boolean) => void;
  onOpenLog: (stand: Stand, item?: QueueItem) => void;
}) {
  if (!stands.length) return <EmptySearch />;
  return (
    <div className="columns-1 md:columns-2 xl:columns-3 2xl:columns-4 gap-3">
      {stands.map((stand) => (
        <StandCard
          key={stand.id}
          stand={stand}
          onOpenQueue={onOpenQueue}
          onSetTesting={onSetTesting}
          onOpenLog={onOpenLog}
        />
      ))}
    </div>
  );
}

function StripsConcept({
  stands,
  onOpenQueue,
  onSetTesting,
  onOpenLog,
}: {
  stands: Stand[];
  onOpenQueue: (standId: number) => void;
  onSetTesting: (standId: number, testing: boolean) => void;
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
                  <span className="text-warn">осталось {stats.pending + stats.running}</span>
                </div>
                <QueueBar queue={stand.queue} />
              </div>
              <div className="flex items-center gap-1 flex-wrap">
                <Button size="sm"
                  type="button"
                  className="inline-flex items-center gap-1"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onSetTesting(stand.id, true);
                  }}
                >
                  <Play className="w-3.5 h-3.5" />
                  Старт
                </Button>
                <Button variant="danger" size="sm"
                  type="button"
                  className="inline-flex items-center gap-1"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onSetTesting(stand.id, false);
                  }}
                >
                  <Square className="w-3.5 h-3.5" />
                  Стоп
                </Button>
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
              <div className="surface-2 border border-token rounded overflow-hidden">
                <div className="border-b border-token px-3 py-2 text-xs text-dim">Лог текущего теста</div>
                <pre className="mono text-xs p-3 overflow-auto max-h-44 whitespace-pre-wrap">{demoQueueLog(stand, current)}</pre>
                <div className="border-t border-token p-2 flex justify-end">
                  <Button size="sm"
                    type="button"
                    className="inline-flex items-center gap-1"
                    onClick={() => onOpenLog(stand, current)}
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                    Открыть журнал целиком
                  </Button>
                </div>
              </div>
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

function LaunchTestModal({
  stands,
  rcOptions,
  onClose,
  onSubmit,
}: {
  stands: Stand[];
  rcOptions: string[];
  onClose: () => void;
  onSubmit: (standId: number, tests: string[], prepareEnv: boolean, debugMode: boolean) => void;
}) {
  const [debugMode, setDebugMode] = useState(false);
  // Штатно доступны только физические стенды — виртуальные существуют
  // исключительно как цель для debug-режима (отладочный запуск теста на ВМ).
  const pickableStands = debugMode ? stands : stands.filter((item) => item.kind !== "virtual");
  const [standId, setStandId] = useState(pickableStands[0]?.id ?? 0);
  const stand = pickableStands.find((item) => item.id === standId) ?? pickableStands[0];
  const tests = stand ? testsForStand(stand, debugMode) : [];
  const [selectedTests, setSelectedTests] = useState<string[]>(tests.slice(0, 2));
  const [prepareEnv, setPrepareEnv] = useState(true);
  const [rcId, setRcId] = useState(rcOptions[0] ?? "");

  const switchStand = (nextStandId: number) => {
    const nextStand = pickableStands.find((item) => item.id === nextStandId) ?? pickableStands[0];
    setStandId(nextStandId);
    setSelectedTests(testsForStand(nextStand, debugMode).slice(0, 2));
  };
  const toggleDebugMode = (enabled: boolean) => {
    setDebugMode(enabled);
    // выключение debug-режима могло сделать выбранный (виртуальный) стенд
    // недоступным — падаем на первый штатный стенд
    const nextStands = enabled ? stands : stands.filter((item) => item.kind !== "virtual");
    const nextStand = nextStands.find((item) => item.id === standId) ?? nextStands[0];
    if (nextStand) {
      setStandId(nextStand.id);
      setSelectedTests(testsForStand(nextStand, enabled).slice(0, 2));
    }
  };
  const toggleTest = (test: string) => {
    setSelectedTests((current) =>
      current.includes(test)
        ? current.filter((item) => item !== test)
        : [...current, test],
    );
  };

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Запустить тест"
      subtitle="Добавление выбранных тестов в очередь стенда"
      width="md"
      footer={
        <>
          <Button type="button" onClick={onClose}>Отмена</Button>
          <Button
            variant="primary"
            type="button"
            disabled={!selectedTests.length || !stand}
            onClick={() => stand && onSubmit(stand.id, selectedTests, prepareEnv, debugMode)}
            title={!selectedTests.length ? "Выберите хотя бы один тест" : undefined}
          >
            Добавить в очередь
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
                options={rcOptions.map((rc) => ({ value: rc, label: rc }))}
                value={rcId}
                onChange={setRcId}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-xs text-dim">Ядро</span>
              <Dropdown
                mode="single"
                options={[{ value: stand?.kernel ?? "", label: stand?.kernel ?? "-" }]}
                value={stand?.kernel ?? ""}
                onChange={() => {}}
                disabled
              />
            </label>
          </div>

          <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
            <Checkbox
              checked={prepareEnv}
              onChange={(event) => setPrepareEnv(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="text-sm font-medium block">Подготовить окружение перед запуском</span>
              <span className="text-xs text-dim">
                Стенд будет приведён к чистому состоянию (testenv prepare) непосредственно перед стартом выбранных тестов
              </span>
            </span>
          </label>

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
            {tests.length ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {tests.map((test) => (
                  <label key={test} className="surface border border-token rounded p-2 flex items-center gap-2 text-sm">
                    <Checkbox checked={selectedTests.includes(test)} onChange={() => toggleTest(test)} />
                    <span>{test}</span>
                  </label>
                ))}
              </div>
            ) : (
              <div className="text-xs text-dim">
                За этим стендом не закреплён ни один тест. Включите debug режим, чтобы выбрать тест из общего каталога.
              </div>
            )}
          </div>

        </div>
    </Modal>
  );
}

function QueueModal({
  stand,
  onClose,
  onChange,
  onOpenLog,
}: {
  stand: Stand;
  onClose: () => void;
  onChange: (queue: QueueItem[]) => void;
  onOpenLog: (item: QueueItem) => void;
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [extraTest, setExtraTest] = useState("");
  // Очередь стенда несёт признак debug-режима в meta уже поставленных задач
  // (см. addTestsToQueue) — отдельное поле на Stand заводить не стали,
  // это дешевле и не расходится с тем, что реально видно в очереди.
  const standDebugMode = stand.queue.some((item) => item.meta.includes("debug mode"));
  const candidateTests = standDebugMode
    ? TEST_CATALOG.map((entry) => entry.name)
    : testsForStand(stand, false);
  const availableExtraTests = candidateTests.filter(
    (test) => !stand.queue.some((item) => item.title === test),
  );

  const reorder = (from: number, to: number) => {
    if (from === to) return;
    const next = [...stand.queue];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };
  const remove = (index: number) => {
    onChange(stand.queue.filter((_, itemIndex) => itemIndex !== index));
  };
  const retry = (index: number) => {
    onChange(
      stand.queue.map((item, itemIndex) =>
        itemIndex === index ? { ...item, state: "pending", meta: "перезапуск поставлен в очередь", knownIssue: undefined } : item,
      ),
    );
  };
  const stop = (index: number) => {
    onChange(
      stand.queue.map((item, itemIndex) =>
        itemIndex === index ? { ...item, state: "failed", meta: "остановлено вручную" } : item,
      ),
    );
  };
  const addExtraTest = () => {
    if (!extraTest) return;
    onChange([
      ...stand.queue,
      {
        title: extraTest,
        state: "pending",
        meta: standDebugMode ? "доп. тест · debug mode" : "доп. тест только для этого стенда",
      },
    ]);
    setExtraTest("");
  };

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
        {availableExtraTests.length > 0 && (
          <div className="surface-2 border border-token rounded p-3 mb-3">
            <div className="text-xs text-dim mb-2">Добавить доп. тест стенда</div>
            <div className="flex items-center gap-2 flex-wrap">
              <Dropdown
                mode="single"
                options={[
                  { value: "", label: "Выбрать тест" },
                  ...availableExtraTests.map((test) => ({ value: test, label: test })),
                ]}
                value={extraTest}
                onChange={setExtraTest}
              />
              <Button
                type="button"
                size="sm"
                onClick={addExtraTest}
                disabled={!extraTest}
                title={!extraTest ? "Выберите тест, доступный этому стенду" : undefined}
              >
                Добавить
              </Button>
            </div>
          </div>
        )}
        <div>
          {stand.queue.length ? (
            <div className="grid gap-2">
              {stand.queue.map((item, index) => (
                <div
                  key={`${item.title}-${index}`}
                  draggable
                  onDragStart={() => setDragIndex(index)}
                  onDragEnter={() => setOverIndex(index)}
                  onDragOver={(event) => event.preventDefault()}
                  onDragEnd={() => {
                    if (dragIndex !== null && overIndex !== null) reorder(dragIndex, overIndex);
                    setDragIndex(null);
                    setOverIndex(null);
                  }}
                  className={`surface-2 border rounded p-3 grid grid-cols-1 lg:grid-cols-[42px_1fr_auto] gap-3 items-center cursor-grab active:cursor-grabbing transition-all duration-200 ${
                    dragIndex === index
                      ? "opacity-50 scale-[0.99] border-accent"
                      : overIndex === index
                        ? "border-accent translate-y-0.5"
                        : "border-token"
                  }`}
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
                    {item.log && <div className="mono text-[11px] text-accent mt-1 truncate">{item.log}</div>}
                  </div>
                  <div className="flex items-center gap-1 flex-wrap justify-start lg:justify-end">
                    {item.log && (
                      <Button size="sm" type="button" className="inline-flex items-center gap-1" onClick={() => onOpenLog(item)}>
                        <ExternalLink className="w-4 h-4" />
                        Лог
                      </Button>
                    )}
                    {item.state === "running" && (
                      <Button variant="danger" size="sm" type="button" className="inline-flex items-center gap-1" onClick={() => stop(index)}>
                        <Square className="w-4 h-4" />
                        Остановить
                      </Button>
                    )}
                    {item.state === "failed" && (
                      <Button size="sm" type="button" className="inline-flex items-center gap-1" onClick={() => retry(index)}>
                        <RefreshCcw className="w-4 h-4" />
                        Ретрай
                      </Button>
                    )}
                    <Button variant="danger" size="sm" type="button" className="inline-flex items-center gap-1" onClick={() => remove(index)}>
                      <Trash2 className="w-4 h-4" />
                      Удалить
                    </Button>
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

function QueueConcept({ stands }: { stands: Stand[] }) {
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
        </div>
      ))}
    </div>
  );
}

function StandCard({
  stand,
  onOpenQueue,
  onSetTesting,
  onOpenLog,
}: {
  stand: Stand;
  onOpenQueue: (standId: number) => void;
  onSetTesting: (standId: number, testing: boolean) => void;
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
          <Button size="sm"
            type="button"
            className="inline-flex items-center justify-center gap-1 px-1.5"
            onClick={() => onSetTesting(stand.id, true)}
            disabled={stand.status === "testing"}
            title={stand.status === "testing" ? "Стенд уже выполняет тест" : undefined}
          >
            <Play className="w-4 h-4 shrink-0" />
            Старт
          </Button>
          <Button variant="danger" size="sm"
            type="button"
            className="inline-flex items-center justify-center gap-1 px-1.5"
            onClick={() => onSetTesting(stand.id, false)}
            disabled={stand.status !== "testing"}
            title={stand.status !== "testing" ? "Остановить можно только запущенный тест" : undefined}
          >
            <Square className="w-4 h-4 shrink-0" />
            Стоп
          </Button>
        </div>

        {detailsOpen && (
          <div className="mt-3 grid gap-2">
            <InfoBox label="Текущий тест" value={current?.title ?? stand.currentTitle} />
            <StandMetricsGrid stand={stand} />
            <div className="surface-2 border border-token rounded overflow-hidden">
              <div className="border-b border-token px-3 py-2 text-xs text-dim">Лог</div>
              <pre className="mono text-xs p-3 overflow-auto max-h-36 whitespace-pre-wrap">{demoQueueLog(stand, current)}</pre>
              <div className="border-t border-token p-2 flex justify-end">
                <Button size="sm"
                  type="button"
                  className="inline-flex items-center gap-1"
                  onClick={() => onOpenLog(stand, current)}
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  Открыть журнал целиком
                </Button>
              </div>
            </div>
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
        <Counter label="Осталось" value={stats.pending + stats.running} className="text-warn" />
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
      <span className="bg-[var(--warn)]" style={{ width: `${(stats.pending / total) * 100}%` }} />
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
        <div key={`${item.title}-${index}`} className="surface-2 border border-token rounded p-3 flex items-start justify-between gap-3">
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
 * Каждый тест закреплён за одним конкретным стендом (`homeStandId`) — в
 * штатном режиме запустить его можно только там. Debug режим (см.
 * `LaunchTestModal`) снимает это ограничение для разового отладочного
 * запуска, но никогда не используется в прогоне (см. `runs.tsx`).
 *
 * `homeStandId` — демо-каталог, привязка тест→стенд по числовому id; в
 * живом режиме реальный каталог тестов (`testDefinitions.ts`) не несёт
 * такой привязки к конкретному стенду вообще — эта модель разъезжается с
 * реальным бэкендом и подлежит пересмотру в волне, вайрящей `tests.tsx`.
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
 * Список тестов, доступных для запуска на стенде. В штатном режиме —
 * только тесты, привязанные к этому стенду (`homeStandId === stand.id`).
 * В debug режиме ограничение по стенду снимается целиком — доступен весь
 * каталог, независимо от того, чей это тест и какой стенд выбран
 * (включая виртуальные стенды, см. `LaunchTestModal`).
 */
function testsForStand(stand: Stand, debugMode = false): string[] {
  if (debugMode) return TEST_CATALOG.map((entry) => entry.name);
  return TEST_CATALOG.filter((entry) => entry.homeStandId === stand.id).map((entry) => entry.name);
}
