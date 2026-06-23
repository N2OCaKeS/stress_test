/**
 * Страница результата задачи `/tasks/:id`.
 *
 * Грузит `getTask(id)` и поллит, пока задача не терминальна (queued/running →
 * спиннер со статусом). По завершении рендерит результат по `kind`:
 *  - `users.inventory` → `InventoryResultView` с рабочими действиями
 *    (связать/импорт/ignore) над `result.{unknown_users, unlinked_existing}`;
 *  - остальное → общий `TaskDetail` из /worker (статус, result, last_error).
 *
 * Раскладку даёт `Shell` без middle-панели — у страницы один таргет, списка
 * слева нет (он живёт в /worker).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { AlertCircle, RotateCw } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { apiErrMsg } from "@/api/client";
import { getTask } from "@/api/server/misc";
import { isTerminalTaskStatus } from "@/api/server/types";
import type {
  TaskRead,
  UnknownUser,
  UnlinkedExistingUser,
} from "@/api/server/types";
import {
  TaskDetail,
  canCancelTask,
  statusBadgeClass,
  TASK_POLL_MS,
} from "@/pages/worker/workerLive";
import { InventoryResultView } from "@/pages/server/InventoryResultView";

/** Kind'ы, чей результат показываем через InventoryResultView. */
function isInventoryKind(kind: string): boolean {
  return kind === "users.inventory";
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function TaskResultPage() {
  const { id } = useParams<{ id: string }>();
  const { persona } = usePersona();
  const [task, setTask] = useState<TaskRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(
    (showSpinner: boolean) => {
      if (!id) return;
      if (showSpinner) setLoading(true);
      getTask(id)
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
    [id],
  );

  // Сброс + initial load при смене id.
  useEffect(() => {
    setTask(null);
    setErr(null);
    load(true);
  }, [id, load]);

  // Поллинг, пока задача не терминальна.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (task && isTerminalTaskStatus(task.status)) return;
      load(false);
    }, TASK_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load, task]);

  let body;
  if (!id) {
    body = <CenteredNote note="Не указан id задачи." danger />;
  } else if (loading && !task) {
    body = <CenteredNote note="Загрузка задачи…" />;
  } else if (err != null && !task) {
    body = (
      <CenteredNote
        note={apiErrMsg(err, "GET /tasks/{id} вернул ошибку")}
        danger
        onRetry={() => load(true)}
      />
    );
  } else if (!task) {
    body = null;
  } else if (!isTerminalTaskStatus(task.status)) {
    body = <PendingTask task={task} />;
  } else if (isInventoryKind(task.kind) && task.status === "succeeded") {
    const result = (task.result ?? {}) as Record<string, unknown>;
    body = (
      <section className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="border-b border-token p-5 flex items-center gap-3 flex-wrap">
          <h1 className="text-xl font-semibold mono truncate">{task.kind}</h1>
          <span className={statusBadgeClass(task.status)}>{task.status}</span>
          <span className="mono text-xs text-dim">{task.id}</span>
        </div>
        <div className="scroll-block p-5">
          <InventoryResultView
            serverId={task.server_id ?? null}
            unknownUsers={asArray<UnknownUser>(result.unknown_users)}
            unlinkedExisting={asArray<UnlinkedExistingUser>(
              result.unlinked_existing,
            )}
            onImported={() => load(false)}
            onLinked={() => load(false)}
          />
        </div>
      </section>
    );
  } else {
    // Прочие задачи (включая инвентаризацию в failed/cancelled) — общий вид.
    body = (
      <TaskDetail
        taskId={id}
        canCancel={canCancelTask(persona)}
        onChanged={() => load(false)}
      />
    );
  }

  return <Shell breadcrumb="server_worker / task">{body}</Shell>;
}

function PendingTask({ task }: { task: TaskRead }) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <RotateCw className="w-10 h-10 mx-auto text-accent mb-3 animate-spin" />
        <div className="text-sm mb-2 mono">{task.kind}</div>
        <div className="text-xs text-dim mb-1">
          Задача в работе (<b>{task.status}</b>) — ждём результат…
        </div>
        <div className="mono text-[11px] text-dim">{task.id}</div>
      </div>
    </section>
  );
}

function CenteredNote({
  note,
  danger,
  onRetry,
}: {
  note: string;
  danger?: boolean;
  onRetry?: () => void;
}) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        {danger && <AlertCircle className="w-10 h-10 mx-auto text-danger mb-3" />}
        <div className="text-sm text-dim mb-3">{note}</div>
        {onRetry && (
          <button className="btn" onClick={onRetry}>
            Повторить
          </button>
        )}
      </div>
    </section>
  );
}
