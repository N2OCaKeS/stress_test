/**
 * Унифицированная панель исхода worker-задачи, отслеживаемой `useTaskOutcome`.
 *
 * Показывает строку «label · task_id · status-badge», спиннер «ждём worker…»
 * пока задача в работе, success-строку по succeeded и красный alert с
 * `last_error` при failed. Раньше эта разметка была скопирована во вкладках
 * manage / ipmi / drift — теперь они рендерят один компонент.
 *
 * Пока задача нетерминальна, рядом со спиннером висит кнопка «Отменить» —
 * шлёт `POST /tasks/{id}/cancel`. Отмена pending/running доступна носителю
 * `(task, cancel)`; на 403/409 показываем ошибку прямо в панели и не сбрасываем
 * трек, чтобы поллинг продолжил подтягивать актуальный статус.
 */
import { useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { cancelTask } from "@/api/server/misc";
import { apiErrMsg } from "@/api/client";
import {
  isTerminalTaskStatus,
  type TrackedTask,
} from "@/api/server/useTaskOutcome";
import { Button } from "@/components/ui/Button";
import { Badge, type BadgeKind } from "@/components/ui/Badge";

interface Props {
  outcome: TrackedTask;
  /**
   * Подпись операции. По умолчанию берётся `outcome.label`; вкладки с
   * единственной задачей передают свой статический текст.
   */
  label?: ReactNode;
  /** Текст success-строки. Дефолт — нейтральный «успешно». */
  successText?: ReactNode;
  /** Доп. классы внешнего контейнера (отступы под конкретную вкладку). */
  className?: string;
  /**
   * Вызывается после успешной отмены — вкладка обычно перечитывает свой стейт
   * (сводку, список) и/или сбрасывает трек. Кнопка «Отменить» не рендерится,
   * если коллбэк не передан.
   */
  onCancelled?: () => void;
}

export function TaskOutcomeBanner({
  outcome,
  label,
  successText = "Задача завершилась успешно.",
  className = "",
  onCancelled,
}: Props) {
  const [cancelling, setCancelling] = useState(false);
  const [cancelErr, setCancelErr] = useState<string | null>(null);
  const tone: BadgeKind =
    outcome.status === "succeeded"
      ? "ok"
      : outcome.status === "failed"
        ? "danger"
        : "warn";
  const cancelable = onCancelled != null && !isTerminalTaskStatus(outcome.status);

  async function handleCancel() {
    if (cancelling) return;
    if (typeof window !== "undefined") {
      const ok = window.confirm(`Отменить задачу ${outcome.taskId}?`);
      if (!ok) return;
    }
    setCancelErr(null);
    setCancelling(true);
    try {
      await cancelTask(outcome.taskId);
      onCancelled?.();
    } catch (e) {
      setCancelErr(apiErrMsg(e, "Не удалось отменить задачу"));
    } finally {
      setCancelling(false);
    }
  }
  return (
    <div
      className={`surface-2 border border-token rounded p-3 text-xs flex flex-col gap-1.5${
        className ? ` ${className}` : ""
      }`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium">{label ?? outcome.label}</span>
        <span className="mono text-dim">{outcome.taskId}</span>
        <Badge kind={tone}>{outcome.status}</Badge>
        {outcome.polling && (
          <span className="flex items-center gap-1 text-dim">
            <RefreshCw className="w-3 h-3 animate-spin" /> ждём worker…
          </span>
        )}
        {cancelable && (
          <Button size="sm"
            type="button"
            className="flex items-center gap-1 ml-auto"
            onClick={handleCancel}
            disabled={cancelling}
            title="Отменить pending/running задачу"
          >
            <XCircle className="w-3.5 h-3.5" />
            {cancelling ? "Отменяем…" : "Отменить"}
          </Button>
        )}
      </div>
      {!outcome.polling && outcome.status === "succeeded" && (
        <div className="flex items-center gap-1 text-ok">
          <CheckCircle2 className="w-3.5 h-3.5" /> {successText}
        </div>
      )}
      {outcome.error && (
        <div className="flex items-start gap-1.5 text-danger">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5" />
          <span className="flex-1">{outcome.error}</span>
        </div>
      )}
      {cancelErr && (
        <div className="flex items-start gap-1.5 text-danger">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5" />
          <span className="flex-1">{cancelErr}</span>
        </div>
      )}
    </div>
  );
}
