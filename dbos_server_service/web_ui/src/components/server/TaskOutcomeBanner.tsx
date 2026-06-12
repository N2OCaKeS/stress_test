/**
 * Унифицированная панель исхода worker-задачи, отслеживаемой `useTaskOutcome`.
 *
 * Показывает строку «label · task_id · status-badge», спиннер «ждём worker…»
 * пока задача в работе, success-строку по succeeded и красный alert с
 * `last_error` при failed. Раньше эта разметка была скопирована во вкладках
 * manage / ipmi / drift — теперь они рендерят один компонент.
 */
import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, RefreshCw } from "lucide-react";
import type { TrackedTask } from "@/api/server/useTaskOutcome";

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
}

export function TaskOutcomeBanner({
  outcome,
  label,
  successText = "Задача завершилась успешно.",
  className = "",
}: Props) {
  const tone =
    outcome.status === "succeeded"
      ? "badge-ok"
      : outcome.status === "failed"
        ? "badge-danger"
        : "badge-warn";
  return (
    <div
      className={`surface-2 border border-token rounded p-3 text-xs flex flex-col gap-1.5${
        className ? ` ${className}` : ""
      }`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium">{label ?? outcome.label}</span>
        <span className="mono text-dim">{outcome.taskId}</span>
        <span className={`badge ${tone}`}>{outcome.status}</span>
        {outcome.polling && (
          <span className="flex items-center gap-1 text-dim">
            <RefreshCw className="w-3 h-3 animate-spin" /> ждём worker…
          </span>
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
    </div>
  );
}
