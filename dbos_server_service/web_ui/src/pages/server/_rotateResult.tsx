/**
 * Разбивка результата worker-rotate пароля server-account'а
 * (`AccountRotateDispatchResponse`).
 *
 * Backend на каждый привязанный сервер либо ставит задачу (`dispatched`), либо
 * пропускает её с причиной (`failed`). В массовом режиме (`mode: "all"`) один
 * битый сервер не валит весь батч: успешные задачи уже в очереди. Если worker
 * отбил dispatch после части успешных постановок — `partial_failure=true` и
 * `next_action="manual_cancel_dispatched"`: автоотката нет, отменять уже
 * отправленные задачи оператор должен вручную по `task_id`.
 *
 * Карточка показывает счётчики dispatched/failed, список задач (имя сервера +
 * статус + переход на `/tasks/{id}`) и список пропусков с человекочитаемой
 * причиной. Используется на вкладке «Аккаунты» сервера и в `ServerUsers`.
 */
import { AlertTriangle, ArrowRight, CheckCircle2, XCircle } from "lucide-react";
import type {
  AccountRotateDispatchResponse,
  AccountRotateSkipped,
} from "@/api/server/accounts";

/** Человекочитаемая причина пропуска по `reason`-коду backend'а. */
const SKIP_REASON_LABELS: Record<string, string> = {
  decommissioned: "сервер выведен из эксплуатации",
  idempotent_conflict: "уже выполняется такая же задача (idempotency)",
  worker_unreachable: "worker недоступен — задача не поставлена",
  not_attempted: "не пытались (батч прерван после ошибки worker'а)",
  not_found_or_cross_dept: "сервер не найден или принадлежит другому отделу",
};

function skipReasonLabel(reason: string): string {
  return SKIP_REASON_LABELS[reason] ?? reason;
}

/**
 * `dispatched`/`failed` — основные поля; `tasks`/`skipped` — их алиасы. Берём
 * первый непустой, чтобы пережить любой из вариантов ответа.
 */
function pickTasks(res: AccountRotateDispatchResponse) {
  return res.dispatched?.length ? res.dispatched : res.tasks;
}

function pickFailed(res: AccountRotateDispatchResponse): AccountRotateSkipped[] {
  return res.failed?.length ? res.failed : res.skipped;
}

export function RotateDispatchResult({
  result,
  serverName,
  onOpenTask,
}: {
  result: AccountRotateDispatchResponse;
  /** Запасное имя сервера, если `server_name` в ответе пустой. */
  serverName?: (id: string) => string;
  /** Переход на страницу задачи `/tasks/{task_id}`. */
  onOpenTask?: (taskId: string) => void;
}) {
  const tasks = pickTasks(result);
  const failed = pickFailed(result);
  const nameOf = (id: string, given: string | null) =>
    given ?? serverName?.(id) ?? id;

  const manualCancel = result.next_action === "manual_cancel_dispatched";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 text-sm flex-wrap">
        <span className="badge badge-accent mono">
          {result.mode === "all" ? "массовая" : "точечная"}
        </span>
        <span className="flex items-center gap-1 text-ok">
          <CheckCircle2 className="w-4 h-4" /> поставлено: {tasks.length}
        </span>
        {failed.length > 0 && (
          <span className="flex items-center gap-1 text-warn">
            <XCircle className="w-4 h-4" /> пропущено: {failed.length}
          </span>
        )}
      </div>

      {(result.partial_failure || manualCancel) && (
        <div className="alert-warn text-xs flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            Часть задач уже отправлена на боксы и продолжит выполнение. Откат
            автоматически НЕ выполняется — если ротацию нужно отменить, делайте
            это вручную по каждой задаче из списка ниже (на странице задачи).
          </span>
        </div>
      )}

      {tasks.length > 0 && (
        <div className="flex flex-col gap-1">
          {tasks.map((t) => (
            <div
              key={t.task_id}
              className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1"
            >
              <CheckCircle2 className="w-3.5 h-3.5 text-ok shrink-0" />
              <span className="flex-1 min-w-0 truncate">
                {nameOf(t.server_id, t.server_name)}
              </span>
              <span className="badge badge-ok text-[10px]">{t.status}</span>
              {onOpenTask ? (
                <button
                  type="button"
                  className="btn btn-sm flex items-center gap-1"
                  onClick={() => onOpenTask(t.task_id)}
                  title="Открыть страницу задачи"
                >
                  <span className="mono text-[11px]">{t.task_id}</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              ) : (
                <span className="mono text-[11px] text-dim">{t.task_id}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {failed.length > 0 && (
        <div className="flex flex-col gap-1">
          {failed.map((s) => (
            <div
              key={s.server_id}
              className="flex items-center gap-2 text-sm border border-token rounded px-2 py-1"
            >
              <XCircle className="w-3.5 h-3.5 text-warn shrink-0" />
              <span className="flex-1 min-w-0 truncate">
                {nameOf(s.server_id, s.server_name)}
              </span>
              <span className="text-xs text-dim text-right">
                {skipReasonLabel(s.reason)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
