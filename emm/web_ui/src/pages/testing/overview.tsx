/**
 * «Рабочая зона» тестирования — список стендов пула + агрегированный
 * дашборд + панель prepare/testenv. Три визуальных концепта одного и того
 * же списка стендов (карточки/полоски/очереди) сохранены как есть — это
 * материал для согласования с руководителем, какой вид удобнее для
 * повседневной работы; окончательный выбор владелец сделает позже.
 */
import { useMemo, useState } from "react";
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
import { LaunchRunModal, RUNS } from "./runs";
import {
  Counter,
  EmptySearch,
  InfoBox,
  KnownIssueBadge,
  LoadMeter,
  LogViewerModal,
  MetaRow,
  ModalHeader,
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
  type Stand,
} from "./_shared";

type ConceptId = "cards" | "strips" | "queue";
type LaunchModal = "test" | "run" | null;
type StandFilter = "all" | "testing" | "busy";

interface LogTarget {
  stand: Stand;
  item?: QueueItem;
}

export function TestingOverview() {
  const [concept, setConcept] = useState<ConceptId>("cards");
  const [filter, setFilter] = useState<StandFilter>("all");
  const [stands, setStands] = useState(STANDS);
  const [queueStandId, setQueueStandId] = useState<number | null>(null);
  const [launchModal, setLaunchModal] = useState<LaunchModal>(null);
  const [logTarget, setLogTarget] = useState<LogTarget | null>(null);
  const [dashboardOpen, setDashboardOpen] = useState(true);

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
  const addTestsToQueue = (standId: number, tests: string[], prepareEnv: boolean) => {
    setStands((current) =>
      current.map((stand) => {
        if (stand.id !== standId) return stand;
        const prepItem: QueueItem[] = prepareEnv
          ? [{ title: "Подготовка окружения", state: "pending", meta: "testenv prepare перед запуском выбранных тестов" }]
          : [];
        const testItems = tests.map<QueueItem>((title) => ({ title, state: "pending", meta: "запустить тест" }));
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
      <FleetDashboard
        stands={stands}
        totalRuns={RUNS.length}
        open={dashboardOpen}
        onToggle={() => setDashboardOpen((v) => !v)}
      />

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold">Рабочая зона тестирования</h2>
          <div className="text-sm text-dim mt-1">{stands.length} стендов, отдельная очередь у каждого стенда</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <button type="button" className="btn btn-sm btn-primary inline-flex items-center gap-2" onClick={() => setLaunchModal("test")}>
            <Play className="w-4 h-4" />
            Запустить тест
          </button>
          <button type="button" className="btn btn-sm inline-flex items-center gap-2" onClick={() => setLaunchModal("run")}>
            <ListChecks className="w-4 h-4" />
            Запустить прогон
          </button>
          <div className="surface border border-token rounded p-1 flex items-center gap-1 flex-wrap">
            {[
              { id: "cards" as const, label: "Карточки", icon: LayoutGrid },
              { id: "strips" as const, label: "Полоски", icon: ListTree },
              { id: "queue" as const, label: "Очереди", icon: BarChart3 },
            ].map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setConcept(item.id)}
                  className={`btn btn-sm inline-flex items-center gap-2 ${concept === item.id ? "btn-primary" : ""}`}
                >
                  <Icon className="w-4 h-4" />
                  <span>{item.label}</span>
                </button>
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
            <button
              key={item.id}
              type="button"
              onClick={() => setFilter(item.id)}
              className={`btn btn-sm inline-flex items-center gap-2 ${filter === item.id ? "btn-primary" : ""}`}
            >
              <span>{item.label}</span>
              <span className="mono text-[11px] opacity-80">{item.count}</span>
            </button>
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
          onClose={() => setLaunchModal(null)}
          onSubmit={(standId, tests, prepareEnv) => {
            addTestsToQueue(standId, tests, prepareEnv);
            setLaunchModal(null);
          }}
        />
      )}
      {launchModal === "run" && <LaunchRunModal onClose={() => setLaunchModal(null)} />}
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
  open,
  onToggle,
}: {
  stands: Stand[];
  totalRuns: number;
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
              {STORAGE_STATS.map((s) => (
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
                <span className={`badge badge-${meta.badge} inline-flex items-center gap-1 shrink-0`}>
                  <Icon className="w-3 h-3" />
                  {meta.label}
                </span>
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
                <button
                  type="button"
                  className="btn btn-sm inline-flex items-center gap-1"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onSetTesting(stand.id, true);
                  }}
                >
                  <Play className="w-3.5 h-3.5" />
                  Старт
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-danger inline-flex items-center gap-1"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onSetTesting(stand.id, false);
                  }}
                >
                  <Square className="w-3.5 h-3.5" />
                  Стоп
                </button>
                <button
                  type="button"
                  className="btn btn-sm inline-flex items-center gap-1"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onOpenQueue(stand.id);
                  }}
                >
                  <ListChecks className="w-3.5 h-3.5" />
                  Очередь
                </button>
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
                  <button
                    type="button"
                    className="btn btn-sm inline-flex items-center gap-1"
                    onClick={() => onOpenLog(stand, current)}
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                    Открыть журнал целиком
                  </button>
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
  onClose,
  onSubmit,
}: {
  stands: Stand[];
  onClose: () => void;
  onSubmit: (standId: number, tests: string[], prepareEnv: boolean) => void;
}) {
  const [standId, setStandId] = useState(stands[0]?.id ?? 0);
  const stand = stands.find((item) => item.id === standId) ?? stands[0];
  const tests = stand ? testsForStand(stand) : [];
  const [selectedTests, setSelectedTests] = useState<string[]>(tests.slice(0, 2));
  const [prepareEnv, setPrepareEnv] = useState(true);

  const switchStand = (nextStandId: number) => {
    const nextStand = stands.find((item) => item.id === nextStandId) ?? stands[0];
    setStandId(nextStandId);
    setSelectedTests(testsForStand(nextStand).slice(0, 2));
  };
  const toggleTest = (test: string) => {
    setSelectedTests((current) =>
      current.includes(test)
        ? current.filter((item) => item !== test)
        : [...current, test],
    );
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/55 flex items-center justify-center p-5">
      <div className="surface border border-token rounded w-full max-w-3xl max-h-[86vh] overflow-hidden shadow-2xl">
        <ModalHeader title="Запустить тест" subtitle="Добавление выбранных тестов в очередь стенда" onClose={onClose} />
        <div className="p-4 grid gap-4 overflow-auto max-h-[calc(86vh-64px)]">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <label className="grid gap-1">
              <span className="text-xs text-dim">Стенд</span>
              <select className="input" value={standId} onChange={(event) => switchStand(Number(event.target.value))}>
                {stands.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            </label>
            <label className="grid gap-1">
              <span className="text-xs text-dim">RC</span>
              <select className="input">
                {RC_IDS.map((rc) => <option key={rc}>{rc}</option>)}
              </select>
            </label>
            <label className="grid gap-1">
              <span className="text-xs text-dim">Ядро</span>
              <select className="input" value={stand?.kernel ?? ""} disabled>
                <option>{stand?.kernel ?? "-"}</option>
              </select>
            </label>
          </div>

          <label className="surface-2 border border-token rounded p-3 flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
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

          <div className="surface-2 border border-token rounded p-3">
            <div className="text-xs text-dim mb-2">Тесты стенда</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {tests.map((test) => (
                <label key={test} className="surface border border-token rounded p-2 flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={selectedTests.includes(test)} onChange={() => toggleTest(test)} />
                  <span>{test}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={onClose}>Отмена</button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!selectedTests.length || !stand}
              onClick={() => stand && onSubmit(stand.id, selectedTests, prepareEnv)}
            >
              Добавить в очередь
            </button>
          </div>
        </div>
      </div>
    </div>
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

  return (
    <div className="fixed inset-0 z-50 bg-black/55 flex items-center justify-center p-5">
      <div className="surface border border-token rounded w-full max-w-4xl max-h-[86vh] overflow-hidden shadow-2xl">
        <ModalHeader title={`Очередь ${stand.name}`} subtitle={stand.ip} onClose={onClose} />
        <div className="p-4 overflow-auto max-h-[calc(86vh-64px)]">
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
                      <span className={`badge badge-${queueBadge(item.state)}`}>{QUEUE_TEXT[item.state]}</span>
                      {item.knownIssue && <KnownIssueBadge issue={item.knownIssue} />}
                    </div>
                    <div className="text-xs text-dim mt-1">{item.meta}</div>
                    {item.log && <div className="mono text-[11px] text-accent mt-1 truncate">{item.log}</div>}
                  </div>
                  <div className="flex items-center gap-1 flex-wrap justify-start lg:justify-end">
                    {item.log && (
                      <button type="button" className="btn btn-sm inline-flex items-center gap-1" onClick={() => onOpenLog(item)}>
                        <ExternalLink className="w-4 h-4" />
                        Лог
                      </button>
                    )}
                    {item.state === "running" && (
                      <button type="button" className="btn btn-sm btn-danger inline-flex items-center gap-1" onClick={() => stop(index)}>
                        <Square className="w-4 h-4" />
                        Остановить
                      </button>
                    )}
                    {item.state === "failed" && (
                      <button type="button" className="btn btn-sm inline-flex items-center gap-1" onClick={() => retry(index)}>
                        <RefreshCcw className="w-4 h-4" />
                        Ретрай
                      </button>
                    )}
                    <button type="button" className="btn btn-sm btn-danger inline-flex items-center gap-1" onClick={() => remove(index)}>
                      <Trash2 className="w-4 h-4" />
                      Удалить
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptySearch text="Очередь пуста" />
          )}
        </div>
      </div>
    </div>
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
      ? "bg-[var(--accent)]"
      : stand.status === "idle"
        ? "bg-[var(--ok)]"
        : stand.status === "manual"
          ? "bg-[var(--warn)]"
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

        <div className="grid grid-cols-4 gap-1.5 mt-3">
          <button
            type="button"
            className={`btn btn-sm inline-flex items-center justify-center gap-1 px-1.5 ${detailsOpen ? "btn-primary" : ""}`}
            onClick={() => setDetailsOpen((open) => !open)}
          >
            Детали
          </button>
          <button
            type="button"
            className="btn btn-sm inline-flex items-center justify-center gap-1 px-1.5"
            onClick={() => onSetTesting(stand.id, true)}
          >
            <Play className="w-4 h-4 shrink-0" />
            Старт
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger inline-flex items-center justify-center gap-1 px-1.5"
            onClick={() => onSetTesting(stand.id, false)}
          >
            <Square className="w-4 h-4 shrink-0" />
            Стоп
          </button>
          <button
            type="button"
            className="btn btn-sm inline-flex items-center justify-center gap-1 px-1.5"
            onClick={() => onOpenQueue(stand.id)}
          >
            <ListChecks className="w-4 h-4 shrink-0" />
            Очередь
          </button>
        </div>

        {detailsOpen && (
          <div className="mt-3 grid gap-2">
            <InfoBox label="Текущий тест" value={current?.title ?? stand.currentTitle} />
            <StandMetricsGrid stand={stand} />
            <div className="surface-2 border border-token rounded overflow-hidden">
              <div className="border-b border-token px-3 py-2 text-xs text-dim">Лог</div>
              <pre className="mono text-xs p-3 overflow-auto max-h-36 whitespace-pre-wrap">{demoQueueLog(stand, current)}</pre>
              <div className="border-t border-token p-2 flex justify-end">
                <button
                  type="button"
                  className="btn btn-sm inline-flex items-center gap-1"
                  onClick={() => onOpenLog(stand, current)}
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  Открыть журнал целиком
                </button>
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
          <span className={`badge badge-${queueBadge(item.state)} shrink-0`}>{QUEUE_TEXT[item.state]}</span>
        </div>
      ))}
    </div>
  );
}

function testsForStand(stand: Stand) {
  const base = [
    "UnixBench 5.1.3",
    "sysbench 1.0.20 / cpu",
    "sysbench 1.0.20 / memory",
    "fio 3.38 / randrw",
    "OpenSSL speed",
  ];
  const serverOnly = ["PostgreSQL TPC-C", "Linpack Xtreme", "PARSEC 3.0 / streamcluster"];
  const workstationOnly = ["7-Zip 24.08", "iperf3 / network", "GUI smoke"];
  return stand.os.includes("Workstation") ? [...base, ...workstationOnly] : [...base, ...serverOnly];
}
