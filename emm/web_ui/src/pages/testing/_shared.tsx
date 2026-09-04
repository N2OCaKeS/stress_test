/**
 * Общие типы, демо-данные и мелкие переиспользуемые компоненты раздела
 * "Тестирование". Раздел целиком — фронтенд-мокап для согласования с
 * руководителем (breadcrumb `testing_service`, реального сервиса ещё нет),
 * поэтому всё состояние здесь — hardcoded demo-массивы, без API-клиентов.
 *
 * `STANDS` и связанные утилиты вынесены сюда (а не в overview.tsx), потому
 * что стенды нужны не только рабочей зоне, но и запуску прогона (выбор
 * подмножества стендов) — без этого пришлось бы тянуть overview.tsx из
 * runs.tsx и получить циклический импорт.
 */
import type { LucideIcon } from "lucide-react";
import { Activity, CheckCircle2, CircleDot, ShieldCheck, X } from "lucide-react";

export type BadgeKind = "ok" | "warn" | "danger" | "accent";
export type StandStatus = "testing" | "manual" | "idle" | "offline";
export type QueueState = "running" | "pending" | "done" | "failed";

export interface KnownIssue {
  ticket: string;
  url: string;
}

export interface QueueItem {
  title: string;
  state: QueueState;
  meta: string;
  log?: string;
  knownIssue?: KnownIssue;
}

export interface StandMetrics {
  cpuUser: number;
  cpuSystem: number;
  cpuTemp: number;
  ram: number;
  diskNvme: number;
  diskSda: number;
  /** последние замеры суммарной загрузки CPU, для sparkline на дашборде */
  history: number[];
}

export interface Stand {
  id: number;
  name: string;
  ip: string;
  status: StandStatus;
  os: string;
  kernel: string;
  currentTitle: string;
  currentMeta: string;
  metrics: StandMetrics;
  queue: QueueItem[];
}

export const STATUS_META: Record<StandStatus, { label: string; icon: LucideIcon; badge: BadgeKind }> = {
  testing: { label: "Тест", icon: Activity, badge: "accent" },
  manual: { label: "Ручная работа", icon: ShieldCheck, badge: "warn" },
  idle: { label: "Свободен", icon: CheckCircle2, badge: "ok" },
  offline: { label: "Недоступен", icon: CircleDot, badge: "danger" },
};

export const QUEUE_TEXT: Record<QueueState, string> = {
  running: "Выполняется",
  pending: "Ожидает",
  done: "Выполнено",
  failed: "Провалено",
};

type QueueTuple = [string, QueueState, string, string?, KnownIssue?];

function makeStand(
  id: number,
  name: string,
  ip: string,
  status: StandStatus,
  os: string,
  kernel: string,
  currentTitle: string,
  currentMeta: string,
  queue: QueueTuple[],
): Stand {
  return {
    id,
    name,
    ip,
    status,
    os,
    kernel,
    currentTitle,
    currentMeta,
    metrics: demoMetrics(id, status),
    queue: queue.map(([title, state, meta, log, knownIssue]) => ({ title, state, meta, log, knownIssue })),
  };
}

function clampPct(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function demoMetrics(id: number, status: StandStatus): StandMetrics {
  if (status === "offline") {
    return { cpuUser: 0, cpuSystem: 0, cpuTemp: 0, ram: 0, diskNvme: 0, diskSda: 0, history: Array(12).fill(0) };
  }
  const base =
    status === "idle" ? 4 + ((id * 3) % 14) : status === "manual" ? 14 + ((id * 7) % 34) : 24 + ((id * 11) % 68);
  const cpuUser = clampPct(base * 0.68);
  const cpuSystem = clampPct(Math.max(base - cpuUser, 2));
  const ram =
    status === "idle" ? 18 + ((id * 5) % 22) : status === "manual" ? 28 + ((id * 9) % 38) : 36 + ((id * 13) % 56);
  const cpuTemp = 36 + ((id * 7) % 34);
  const diskNvme = 18 + ((id * 6) % 62);
  const diskSda = 8 + ((id * 4) % 48);
  const history = Array.from({ length: 12 }, (_, i) => clampPct(base + Math.sin(i * 1.3 + id) * 14));
  return { cpuUser, cpuSystem, cpuTemp, ram, diskNvme, diskSda, history };
}

export const STANDS: Stand[] = [
  makeStand(1, "stand1-201", "10.177.103.201", "testing", "ALT Server 11.0", "6.12.24-1.el11", "UnixBench 5.1.3", "CPU benchmark · 68% · этап 3/5", [
    ["UnixBench 5.1.3", "running", "текущий тест"],
    ["sysbench 1.0.20 / memory", "pending", "следующий"],
    ["fio 3.38 / randread", "pending", "после memory"],
    ["OpenSSL speed", "done", "завершен 12:42", "/logs/stand1/openssl.txt"],
  ]),
  makeStand(2, "stand2-202", "10.177.103.202", "manual", "ALT Workstation 11", "6.12.18-std-def", "ivan.petrov", "ручная работа · отладка GUI · 01:42", [
    ["7-Zip 24.08", "pending", "ожидает освобождения стенда"],
    ["fio 3.38 / seqwrite", "pending", "после 7-Zip"],
  ]),
  makeStand(3, "stand3-59-204", "10.177.103.204", "testing", "ALT Server 10.4", "6.6.63-un-def", "PARSEC 3.0", "blackscholes · 41% · этап 2/4", [
    ["PARSEC 3.0 / blackscholes", "running", "текущий тест"],
    ["PARSEC 3.0 / streamcluster", "pending", "следующий"],
    ["PARSEC 3.0 / canneal", "pending", "в очереди"],
    ["sysbench / cpu", "done", "завершен 12:18", "/logs/stand3/sysbench-cpu.txt"],
  ]),
  makeStand(4, "stand4-58-203", "10.177.103.203", "testing", "ALT Server 11.0", "6.12.24-1.el11", "fs_mark 3.3", "filesystem · 83% · этап 1/3", [
    ["fs_mark 3.3", "running", "текущий тест"],
    ["fio 3.38 / randrw", "pending", "следующий"],
    ["OpenSSL speed", "pending", "после fio"],
  ]),
  makeStand(5, "stand5-91-205", "10.177.103.205", "testing", "ALT Server 11.0", "6.12.24-1.el11", "Linpack Xtreme", "matrix solve · 16% · после ошибки очередь продолжилась", [
    ["PostgreSQL TPC-C", "failed", "ошибка в 12:31", "/logs/stand5/postgresql.txt", { ticket: "DEVQA-4821", url: "https://jira.astralinux.ru/browse/DEVQA-4821" }],
    ["Linpack Xtreme", "running", "текущий тест"],
    ["sysbench / fileio", "pending", "следующий"],
    ["7-Zip 24.08", "pending", "в очереди"],
  ]),
  makeStand(6, "stand6-101", "10.177.103.101", "idle", "ALT Workstation 10.4", "6.1.99-std-def", "Нет активной работы", "стенд свободен", []),
  makeStand(7, "stand7-102", "10.177.103.102", "idle", "ALT Workstation 10.4", "6.1.99-std-def", "Нет активной работы", "стенд свободен", [
    ["sysbench / threads", "pending", "можно стартовать вручную"],
  ]),
  makeStand(8, "stand8-103", "10.177.103.103", "manual", "ALT Workstation 11", "6.12.18-std-def", "qa.sidorov", "ручная работа · отладка драйвера · 00:27", [
    ["OpenSSL speed", "pending", "ожидает освобождения стенда"],
    ["fio / seqread", "pending", "в очереди"],
  ]),
  makeStand(9, "stand9-104", "10.177.103.104", "testing", "ALT Server 11.0", "6.12.24-1.el11", "sysbench 1.0.20", "memory · 22% · этап 1/5", [
    ["sysbench 1.0.20 / memory", "running", "текущий тест"],
    ["sysbench 1.0.20 / cpu", "pending", "следующий"],
    ["sysbench 1.0.20 / mutex", "pending", "в очереди"],
    ["UnixBench 5.1.3", "done", "завершен 12:11", "/logs/stand9/unixbench.txt"],
  ]),
  makeStand(10, "stand10-105", "10.177.103.105", "idle", "ALT Server 11.0", "6.12.24-1.el11", "Нет активной работы", "очередь пуста", []),
  makeStand(11, "stand11-106", "10.177.103.106", "testing", "ALT Server 10.4", "6.6.63-un-def", "fio 3.38", "randrw · 57% · этап 2/4", [
    ["fio 3.38 / randrw", "running", "текущий тест"],
    ["fio 3.38 / seqread", "pending", "следующий"],
    ["fio 3.38 / seqwrite", "pending", "после seqread"],
    ["7-Zip 24.08", "done", "завершен 11:54", "/logs/stand11/7zip.txt"],
  ]),
  makeStand(12, "stand12-107", "10.177.103.107", "manual", "ALT Server 11.0", "6.12.24-1.el11", "admin", "ручная работа · обновление окружения · 00:54", [
    ["fio / verify", "pending", "ожидает завершения ручной работы"],
    ["sysbench / oltp-read-write", "pending", "в очереди"],
  ]),
  makeStand(13, "stand13-108", "10.177.103.108", "testing", "ALT Workstation 11", "6.12.18-std-def", "7-Zip 24.08", "compression · 74% · этап 2/3", [
    ["7-Zip 24.08", "running", "текущий тест"],
    ["OpenSSL speed", "pending", "следующий"],
    ["fio / seqread", "failed", "ошибка в 11:50", "/logs/stand13/fio-seqread.txt", { ticket: "DEVQA-4790", url: "https://jira.astralinux.ru/browse/DEVQA-4790" }],
    ["sysbench / cpu", "done", "завершен 11:37", "/logs/stand13/sysbench-cpu.txt"],
  ]),
  makeStand(14, "stand14-109", "10.177.103.109", "idle", "ALT Workstation 11", "6.12.18-std-def", "Нет активной работы", "стенд свободен", [
    ["UnixBench 5.1.3", "pending", "можно стартовать"],
  ]),
  makeStand(15, "stand15-110", "10.177.103.110", "testing", "ALT Server 11.0", "6.12.24-1.el11", "OpenSSL speed", "aes-256-gcm · 49% · этап 3/4", [
    ["OpenSSL speed", "running", "текущий тест"],
    ["PostgreSQL TPC-C", "pending", "следующий"],
    ["Linpack Xtreme", "failed", "ошибка в 11:21", "/logs/stand15/linpack.txt", { ticket: "DEVQA-4655", url: "https://jira.astralinux.ru/browse/DEVQA-4655" }],
    ["sysbench / cpu", "done", "завершен 11:02", "/logs/stand15/sysbench-cpu.txt"],
  ]),
  makeStand(16, "stand16-111", "10.177.103.111", "offline", "-", "-", "Недоступен", "агент не отвечает 3 мин", [
    ["fio / randrw", "pending", "ожидает восстановления"],
    ["sysbench / memory", "pending", "в очереди"],
  ]),
  makeStand(17, "stand17-112", "10.177.103.112", "idle", "ALT Server 10.4", "6.6.63-un-def", "Нет активной работы", "стенд свободен", []),
  makeStand(18, "stand18-113", "10.177.103.113", "manual", "ALT Server 11.0", "6.12.24-1.el11", "dev.kuznetsov", "ручная работа · сборка пакета · 02:13", [
    ["OpenSSL speed", "pending", "ожидает освобождения"],
    ["fio / verify", "pending", "в очереди"],
    ["7-Zip 24.08", "done", "завершен 10:46", "/logs/stand18/7zip.txt"],
  ]),
  makeStand(19, "stand19-114", "10.177.103.114", "testing", "ALT Workstation 11", "6.12.18-std-def", "OpenSSL speed", "aes-256-gcm · 49% · этап 1/2", [
    ["OpenSSL speed", "running", "текущий тест"],
    ["UnixBench 5.1.3", "pending", "следующий"],
    ["PARSEC 3.0 / canneal", "pending", "в очереди"],
  ]),
  makeStand(20, "stand20-115", "10.177.103.115", "idle", "ALT Server 11.0", "6.12.24-1.el11", "Нет активной работы", "очередь подготовлена", [
    ["sysbench / oltp-read-only", "pending", "готов к старту"],
    ["fio / randread", "pending", "в очереди"],
  ]),
];

export function queueStats(queue: QueueItem[]) {
  return queue.reduce(
    (acc, item) => {
      acc[item.state] += 1;
      return acc;
    },
    { done: 0, failed: 0, pending: 0, running: 0 } satisfies Record<QueueState, number>,
  );
}

export function queueBadge(state: QueueState): BadgeKind {
  if (state === "done") return "ok";
  if (state === "failed") return "danger";
  if (state === "running") return "accent";
  return "warn";
}

export function currentQueueItem(stand: Stand) {
  return (
    stand.queue.find((item) => item.state === "running") ??
    stand.queue.find((item) => item.state === "failed") ??
    stand.queue[0]
  );
}

// ── общие презентационные кусочки ──────────────────────────────────────────

export function DataTable({
  title,
  icon: Icon,
  columns,
  rows,
}: {
  title: string;
  icon: LucideIcon;
  columns: string[];
  rows: string[][];
}) {
  return (
    <div className="surface border border-token rounded overflow-hidden">
      <div className="border-b border-token p-3 flex items-center gap-2">
        <Icon className="w-4 h-4 text-accent" />
        <div className="text-sm font-medium">{title}</div>
      </div>
      <div className="overflow-auto">
        <table className="mini">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.join("-")}>
                {row.map((cell, index) => (
                  <td key={`${cell}-${index}`} className={index === 0 ? "mono" : ""}>
                    {index === row.length - 1 ? <TextStatusBadge value={cell} /> : cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Stat({
  title,
  value,
  icon: Icon,
  kind,
}: {
  title: string;
  value: string;
  icon: LucideIcon;
  kind?: "ok" | "warn" | "danger";
}) {
  const color = kind === "ok" ? "text-ok" : kind === "warn" ? "text-warn" : kind === "danger" ? "text-danger" : "text-accent";
  return (
    <div className="surface border border-token rounded p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="stat-label">{title}</div>
          <div className="stat-big">{value}</div>
        </div>
        <Icon className={`w-5 h-5 ${color}`} />
      </div>
    </div>
  );
}

export function StatusBadge({ status }: { status: StandStatus }) {
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  return (
    <span className={`badge badge-${meta.badge} inline-flex items-center gap-1`}>
      <Icon className="w-3 h-3" />
      {meta.label}
    </span>
  );
}

export function TextStatusBadge({ value }: { value: string }) {
  const kind =
    value === "ready" || value === "passed" || value === "approved" || value === "pass"
      ? "ok"
      : value === "failed" || value === "blocked" || value === "fail"
        ? "danger"
        : value === "running" || value === "testing"
          ? "accent"
          : "warn";
  return <span className={`badge badge-${kind}`}>{value}</span>;
}

export function Counter({ label, value, className }: { label: string; value: number; className: string }) {
  return (
    <div className="surface border border-token rounded p-2">
      <div className="text-[11px] text-dim">{label}</div>
      <div className={`mono font-semibold mt-1 ${className}`}>{value}</div>
    </div>
  );
}

export function InfoBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="surface-2 border border-token rounded p-3">
      <div className="text-xs text-dim mb-1">{label}</div>
      <div className="mono text-sm truncate">{value}</div>
    </div>
  );
}

export function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[54px_minmax(0,1fr)] gap-2">
      <span className="text-dim">{label}</span>
      <span className="mono truncate">{value}</span>
    </div>
  );
}

export function LoadMeter({
  label,
  value,
  compact,
}: {
  label: string;
  value: number;
  compact?: boolean;
}) {
  const color = value >= 85 ? "bg-[var(--danger)]" : value >= 65 ? "bg-[var(--warn)]" : "bg-[var(--accent)]";
  return (
    <div className="min-w-0">
      <div className={`flex items-center justify-between gap-2 ${compact ? "text-[10px]" : "text-[11px]"} mb-1`}>
        <span className="text-dim">{label}</span>
        <span className="mono">{value}%</span>
      </div>
      <div className="h-1.5 rounded-full surface-2 border border-token overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

export function EmptySearch({ text }: { text?: string }) {
  return (
    <div className="surface border border-token rounded p-8 text-center text-dim">
      {text ?? "Нет стендов по выбранным фильтрам"}
    </div>
  );
}

export function ModalHeader({
  title,
  subtitle,
  onClose,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
}) {
  return (
    <div className="border-b border-token px-4 py-3 flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="font-semibold truncate">{title}</div>
        <div className="text-xs text-dim truncate">{subtitle}</div>
      </div>
      <button type="button" className="btn btn-sm" onClick={onClose} aria-label="Закрыть">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

/** Мини-график CSS/SVG без внешних зависимостей — для карточек и дашборда. */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (!values.length) return null;
  const max = Math.max(...values, 1);
  const points = values
    .map((v, i) => `${(i / (values.length - 1 || 1)) * 100},${100 - (v / max) * 100}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className={className ?? "w-full h-6 text-accent"}>
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={4} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Бейдж известной проблемы у упавшего элемента очереди — ссылка на demo-тикет. */
export function KnownIssueBadge({ issue }: { issue: KnownIssue }) {
  return (
    <a
      href={issue.url}
      target="_blank"
      rel="noreferrer"
      className="badge badge-warn inline-flex items-center gap-1"
      title={`Известная проблема · ${issue.ticket}`}
      onClick={(e) => e.stopPropagation()}
    >
      {issue.ticket}
    </a>
  );
}
