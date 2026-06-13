/**
 * Live-составляющие раздела server_worker: общий список задач (Aside) и
 * карточка детали (Workzone), общие хелперы статусов/иконок/поллинга.
 *
 * Используется и обычной страницей /worker (`Worker.tsx`), и /worker/dlq
 * (`WorkerDlq.tsx`) — у них одна раскладка, отличается лишь дефолтный фильтр и
 * акценты (DLQ фокусируется на last_error). Backend-контракт:
 *   GET  /api/server/v1/tasks?status=&kind=&server_id=&limit=&offset=
 *   GET  /api/server/v1/tasks/{id}
 *   POST /api/server/v1/tasks/{id}/cancel
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Search,
  AlertCircle,
  AlertTriangle,
  Server as ServerIcon,
  Box,
  Terminal,
  Zap,
  HardDrive,
  Cog,
  ClipboardList,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useDeptLabel, useServerLabel } from "@/lib/labels";
import { formatMsk } from "@/lib/datetime";
import { apiErrMsg } from "@/api/client";
import { getTask, cancelTask, listTasks } from "@/api/server/misc";
import type { Persona } from "@/types/persona";
import type { ListTasksQuery, TaskRead, TaskStatus } from "@/api/server/types";

export const TASK_POLL_MS = 10_000;

/** Статусы, на которых отмена ещё имеет смысл. */
export function isCancelable(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

/**
 * Право нажать Cancel в server-зоне. Backend перепроверит `(task, cancel)` —
 * клиентский gate только прячет заведомо отбойную кнопку. Грант `(task,
 * cancel)` по дефолту выдан ровно роли `admin` (см. миграцию seed_task_cancel_grant:
 * «отмена чужой task'и — операция уровня admin'а»). operator/reader его не
 * получают, поэтому кнопку им не показываем — иначе клик гарантированно
 * упрётся в 403. dep_admin своего отдела ходит как server.admin, поэтому
 * допускается.
 */
export function canCancelTask(persona: Persona): boolean {
  if (persona.platform_role === "dep_admin") return true;
  return persona.service_roles.server === "admin";
}

const STATUS_BADGE: Record<string, string> = {
  queued: "badge",
  running: "badge badge-warn",
  succeeded: "badge badge-ok",
  failed: "badge badge-danger",
  cancelled: "badge",
};

export function statusBadgeClass(status: TaskStatus): string {
  return STATUS_BADGE[status] ?? "badge";
}

/** Иконка по kind'у (по префиксу до точки). */
export function kindIcon(kind: string): LucideIcon {
  if (kind.startsWith("power")) return Zap;
  if (kind.startsWith("ssh")) return Terminal;
  if (kind.startsWith("installed_packages")) return Box;
  if (kind.startsWith("users") || kind.startsWith("account")) return ClipboardList;
  if (kind.startsWith("inventory") || kind.startsWith("disk")) return HardDrive;
  if (kind.startsWith("server") || kind.startsWith("ipmi")) return Cog;
  return Cog;
}

/** Человеко-читаемая длительность между start и finish (или «—»). */
export function taskDuration(task: TaskRead): string {
  const start = task.started_at ?? task.created_at;
  const end = task.finished_at;
  if (!start || !end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)} s`;
  const min = Math.floor(sec / 60);
  const rest = Math.round(sec % 60);
  return `${min}m ${rest}s`;
}

// ── Aside (task list) ────────────────────────────────────────────────────────

export interface TaskListProps {
  tasks: TaskRead[];
  total: number;
  loading: boolean;
  error: unknown;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRetry: () => void;
  // фильтры
  search: string;
  onSearch: (v: string) => void;
  statusFilter: string;
  onStatusFilter: (v: string) => void;
  kindFilter: string;
  onKindFilter: (v: string) => void;
  /** Скрыть фильтр статуса (DLQ зафиксирован на failed). */
  hideStatusFilter?: boolean;
  /** Список сужен до одного сервера (пришли из карточки сервера). */
  serverScopeId?: string | null;
  onClearServerScope?: () => void;
  onLoadMore?: () => void;
  loadingMore?: boolean;
}

const STATUS_OPTIONS = [
  ["", "status: all"],
  ["queued", "queued"],
  ["running", "running"],
  ["succeeded", "succeeded"],
  ["failed", "failed"],
  ["cancelled", "cancelled"],
] as const;

const KIND_OPTIONS = [
  ["", "kind: all"],
  ["power.on", "power.on"],
  ["power.off", "power.off"],
  ["power.reboot", "power.reboot"],
  ["power.status", "power.status"],
  ["installed_packages.list", "installed_packages.list"],
  ["inventory.sync", "inventory.sync"],
  ["users.inventory", "users.inventory"],
  ["server.prepare", "server.prepare"],
  ["account.provision", "account.provision"],
  ["account.rotate_password", "account.rotate_password"],
  ["ipmi.rotate_password", "ipmi.rotate_password"],
] as const;

export function TaskListAside({
  tasks,
  total,
  loading,
  error,
  selectedId,
  onSelect,
  onRetry,
  search,
  onSearch,
  statusFilter,
  onStatusFilter,
  kindFilter,
  onKindFilter,
  hideStatusFilter,
  serverScopeId,
  onClearServerScope,
  onLoadMore,
  loadingMore,
}: TaskListProps) {
  const term = search.trim().toLowerCase();
  const filtered = term
    ? tasks.filter(
        (t) =>
          t.id.toLowerCase().includes(term) ||
          t.kind.toLowerCase().includes(term) ||
          (t.server_id?.toLowerCase().includes(term) ?? false) ||
          (t.server_hostname?.toLowerCase().includes(term) ?? false) ||
          (t.account_login?.toLowerCase().includes(term) ?? false),
      )
    : tasks;

  return (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${tasks.length} задачам…`}
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
        </div>
        <div className="mt-2 flex items-center gap-1.5 text-xs flex-wrap">
          {!hideStatusFilter && (
            <select
              className="surface-2 border border-token rounded px-2 py-0.5 text-dim"
              value={statusFilter}
              onChange={(e) => onStatusFilter(e.target.value)}
              title="Фильтр по статусу"
            >
              {STATUS_OPTIONS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          )}
          <select
            className="surface-2 border border-token rounded px-2 py-0.5 text-dim"
            value={kindFilter}
            onChange={(e) => onKindFilter(e.target.value)}
            title="Фильтр по типу"
          >
            {KIND_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        {serverScopeId && (
          <ServerScopeChip
            serverId={serverScopeId}
            onClear={onClearServerScope}
          />
        )}
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {!loading && error != null && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(error, "Список задач не загрузился")}</div>
              <button className="btn btn-ghost mt-2" onClick={onRetry}>
                Повторить
              </button>
            </div>
          </div>
        )}
        {!loading && error == null && filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            Задач нет.
          </div>
        )}
        <div className="px-2 flex flex-col gap-0.5">
          {filtered.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              active={selectedId === t.id}
              onSelect={() => onSelect(t.id)}
            />
          ))}
        </div>
        {!loading && error == null && (
          <TruncationNotice
            shown={tasks.length}
            total={total}
            onLoadMore={onLoadMore}
            loadingMore={loadingMore}
            className="mx-3 mt-2"
          />
        )}
      </div>
    </aside>
  );
}

function ServerScopeChip({
  serverId,
  onClear,
}: {
  serverId: string;
  onClear?: () => void;
}) {
  const label = useServerLabel(serverId);
  return (
    <div className="mt-2 flex items-center gap-2 text-[11px] surface-2 border border-token rounded px-2 py-1">
      <ServerIcon className="w-3.5 h-3.5 text-accent shrink-0" />
      <span className="flex-1 min-w-0 truncate" title={serverId}>
        только задачи <b className="mono">{label}</b>
      </span>
      {onClear && (
        <button
          type="button"
          className="btn btn-ghost flex items-center gap-1 shrink-0"
          onClick={onClear}
          title="Показать задачи всего отдела"
        >
          <XCircle className="w-3.5 h-3.5" /> все
        </button>
      )}
    </div>
  );
}

function TaskRow({
  task,
  active,
  onSelect,
}: {
  task: TaskRead;
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = kindIcon(task.kind);
  const deptLabel = useDeptLabel(task.department_id ?? null);
  const serverLabel = useServerLabel(task.server_id ?? null);
  // server_hostname приходит уже резолвленным от server_service; если его нет —
  // падаем на label-резолв по server_id.
  const serverName = task.server_hostname ?? serverLabel;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left ${active ? "active" : ""}`}
    >
      <div className="flex items-center gap-2">
        <Icon className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate mono">
            {task.kind}
            {task.server_id && (
              <span className="text-dim" title={task.server_id}>
                {" "}
                → {serverName}
              </span>
            )}
          </div>
          <div className="text-[11px] text-dim flex items-center gap-2 flex-wrap">
            {task.department_id && (
              <>
                <span className="truncate">{deptLabel}</span>
                <span>·</span>
              </>
            )}
            <span>{formatMsk(task.created_at)}</span>
            <span>·</span>
            <span>{taskDuration(task)}</span>
          </div>
        </div>
        <span className={statusBadgeClass(task.status)}>{task.status}</span>
      </div>
    </button>
  );
}

// ── Workzone (task detail) ───────────────────────────────────────────────────

/**
 * Деталь задачи: тянет полный `TaskRead` (с result/last_error) и поллит, пока
 * задача не терминальна. Cancel — через `cancelTask`, с confirm и gate'ом по
 * ролям.
 */
export function TaskDetail({
  taskId,
  canCancel,
  onChanged,
}: {
  taskId: string;
  canCancel: boolean;
  onChanged?: () => void;
}) {
  const [task, setTask] = useState<TaskRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  const [cancelling, setCancelling] = useState(false);
  const aliveRef = useRef(true);
  const serverLabel = useServerLabel(task?.server_id ?? null);
  const serverName = task?.server_hostname ?? serverLabel;

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(
    (showSpinner: boolean) => {
      if (showSpinner) setLoading(true);
      getTask(taskId)
        .then((t) => {
          if (!aliveRef.current) return;
          setTask(t);
          setErr(null);
        })
        .catch((e: unknown) => {
          if (aliveRef.current) setErr(e);
        })
        .finally(() => {
          if (aliveRef.current) setLoading(false);
        });
    },
    [taskId],
  );

  // Сброс при смене выбранной задачи + initial load.
  useEffect(() => {
    setTask(null);
    setErr(null);
    load(true);
  }, [taskId, load]);

  // Поллинг детали, пока task нетерминальна.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (task && !isCancelable(task.status)) return;
      load(false);
    }, TASK_POLL_MS);
    return () => window.clearInterval(id);
  }, [load, task]);

  async function handleCancel() {
    if (!task || cancelling) return;
    if (typeof window !== "undefined") {
      const ok = window.confirm(
        `Отменить задачу ${task.kind} (${task.id})?`,
      );
      if (!ok) return;
      const reason = window.prompt("Причина отмены (опционально):", "");
      if (reason === null) return;
      setCancelling(true);
      try {
        await cancelTask(task.id, reason.trim() ? { reason: reason.trim() } : undefined);
        load(false);
        onChanged?.();
      } catch (e) {
        if (aliveRef.current) setErr(e);
      } finally {
        if (aliveRef.current) setCancelling(false);
      }
    }
  }

  if (loading && !task) {
    return (
      <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
        <div className="text-sm text-dim">Загрузка задачи…</div>
      </section>
    );
  }

  if (err != null && !task) {
    return (
      <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
        <div className="empty-card max-w-md text-center">
          <AlertCircle className="w-10 h-10 mx-auto text-danger mb-3" />
          <div className="text-sm mb-2">Задача не загрузилась</div>
          <div className="text-xs text-dim mb-3">
            {apiErrMsg(err, "GET /tasks/{id} вернул ошибку")}
          </div>
          <button className="btn" onClick={() => load(true)}>
            Повторить
          </button>
        </div>
      </section>
    );
  }

  if (!task) return null;

  const Icon = kindIcon(task.kind);
  const cancelable = isCancelable(task.status);

  return (
    <section className="flex-1 overflow-hidden flex flex-col min-w-0">
      <div className="border-b border-token p-5 flex items-start gap-4">
        <div className="w-12 h-12 rounded bg-accent flex items-center justify-center shrink-0">
          <Icon className="w-7 h-7" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold truncate mono">{task.kind}</h1>
            <span className={statusBadgeClass(task.status)}>{task.status}</span>
            <span className="text-xs text-dim">
              retry <b>{task.retry_count}</b>
            </span>
          </div>
          <div className="text-sm text-dim mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono text-xs">{task.id}</span>
            {task.server_id && (
              <>
                <span>·</span>
                <span title={task.server_id}>
                  <ServerIcon className="w-3 h-3 inline" /> target:{" "}
                  <b className="mono">{serverName}</b>
                </span>
              </>
            )}
          </div>
        </div>
        {canCancel && cancelable && (
          <div className="shrink-0">
            <button
              className="btn btn-danger flex items-center gap-1"
              onClick={handleCancel}
              disabled={cancelling}
              title="Отменить pending/running задачу"
            >
              <XCircle className="w-4 h-4" />
              {cancelling ? "Отменяем…" : "Cancel"}
            </button>
          </div>
        )}
      </div>

      <div className="scroll-block p-5 flex flex-col gap-5">
        {err != null && (
          <div className="alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">{apiErrMsg(err, "Ошибка")}</div>
          </div>
        )}

        <div className="surface border border-token rounded-lg p-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <DetailField label="status" value={task.status} />
          <DetailField label="kind" value={task.kind} mono />
          <DetailField label="retry_count" value={String(task.retry_count)} />
          <DetailField
            label="server"
            value={
              task.server_hostname ??
              (task.server_id ? serverName : "—")
            }
            hint={task.server_hostname ? task.server_id ?? undefined : undefined}
            mono
          />
          <DetailField
            label="account"
            value={task.account_login ?? task.account_id ?? "—"}
            hint={task.account_login ? task.account_id ?? undefined : undefined}
            mono
          />
          <DetailField label="department_id" value={task.department_id ?? "—"} mono />
          <DetailField label="created_at" value={formatMsk(task.created_at)} />
          <DetailField label="started_at" value={formatMsk(task.started_at)} />
          <DetailField label="finished_at" value={formatMsk(task.finished_at)} />
          <DetailField label="duration" value={taskDuration(task)} />
        </div>

        {task.status === "failed" && task.last_error && (
          <div>
            <div className="text-xs uppercase tracking-wider text-dim mb-2 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-danger" /> last_error
            </div>
            <div className="alert-block mono text-xs whitespace-pre-wrap overflow-x-auto">
              {task.last_error}
            </div>
          </div>
        )}

        <div>
          <div className="text-xs uppercase tracking-wider text-dim mb-2 flex items-center gap-2">
            <Cog className="w-4 h-4" /> result
          </div>
          <pre className="mono text-xs surface-2 border border-token rounded p-3 overflow-x-auto leading-relaxed whitespace-pre-wrap">
            {task.result != null
              ? JSON.stringify(task.result, null, 2)
              : "— (нет результата)"}
          </pre>
        </div>
      </div>
    </section>
  );
}

function DetailField({
  label,
  value,
  mono,
  hint,
}: {
  label: string;
  value: string;
  mono?: boolean;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="text-[11px] uppercase text-dim">{label}</span>
      <span className={`truncate ${mono ? "mono text-xs" : ""}`}>{value}</span>
      {hint && (
        <span className="mono text-[10px] text-dim truncate" title={hint}>
          {hint}
        </span>
      )}
    </div>
  );
}

export function EmptyDetail({ note }: { note?: string }) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <ClipboardList className="w-10 h-10 mx-auto text-dim mb-3" />
        <div className="text-sm text-dim">
          {note ?? "Выберите задачу слева для просмотра деталей."}
        </div>
      </div>
    </section>
  );
}

// ── shared list state (poll + pagination) ───────────────────────────────────

export interface TaskListState {
  tasks: TaskRead[];
  total: number;
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  refetch: () => void;
  loadMore: () => void;
}

/**
 * Грузит страницу задач по фильтрам, поллит каждые ~10с и умеет догружать
 * следующую страницу (offset += limit). Поллинг останавливается на unmount.
 * `fixedStatus` (DLQ → `failed`) перетирает фильтр статуса.
 */
export function useTaskList(
  query: { status: string; kind: string; serverId?: string },
  opts: { enabled: boolean; fixedStatus?: TaskStatus; limit?: number },
): TaskListState {
  const limit = opts.limit ?? 50;
  const effStatus = opts.fixedStatus ?? query.status;
  const serverId = query.serverId || undefined;
  const [tasks, setTasks] = useState<TaskRead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(opts.enabled);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [count, setCount] = useState(limit);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const fetchPage = useCallback(
    (take: number, showSpinner: boolean) => {
      if (!opts.enabled) {
        setLoading(false);
        return;
      }
      if (showSpinner) setLoading(true);
      const q: ListTasksQuery = {
        status: (effStatus || undefined) as ListTasksQuery["status"],
        kind: (query.kind || undefined) as ListTasksQuery["kind"],
        server_id: serverId,
        limit: take,
        offset: 0,
      };
      listTasks(q)
        .then((res) => {
          if (!aliveRef.current) return;
          setTasks(res.items);
          setTotal(res.total);
          setError(null);
        })
        .catch((e: unknown) => {
          if (aliveRef.current) setError(e);
        })
        .finally(() => {
          if (aliveRef.current) {
            setLoading(false);
            setLoadingMore(false);
          }
        });
    },
    [opts.enabled, effStatus, query.kind, serverId],
  );

  // Сброс окна при смене фильтров.
  useEffect(() => {
    setCount(limit);
  }, [effStatus, query.kind, serverId, limit]);

  // Initial + on-filter load.
  useEffect(() => {
    fetchPage(count, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchPage, count]);

  // Polling (без spinner — тихо обновляет текущее окно).
  useEffect(() => {
    if (!opts.enabled) return;
    const id = window.setInterval(() => fetchPage(count, false), TASK_POLL_MS);
    return () => window.clearInterval(id);
  }, [opts.enabled, fetchPage, count]);

  const refetch = useCallback(() => fetchPage(count, true), [fetchPage, count]);
  const loadMore = useCallback(() => {
    setLoadingMore(true);
    setCount((c) => c + limit);
  }, [limit]);

  return { tasks, total, loading, loadingMore, error, refetch, loadMore };
}

export function BlockedDetail() {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <AlertCircle className="w-10 h-10 mx-auto text-warn mb-3" />
        <div className="text-sm font-medium mb-2">
          Раздел недоступен для платформенного администратора
        </div>
        <div className="text-xs text-dim">
          server_worker — часть server-зоны. Учётка <b>account_admin</b> /{" "}
          <b>logging_admin</b> не имеет доступа к задачам серверов — работайте
          под департаментной ролью (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}
