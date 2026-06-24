/**
 * Компактная панель «Задачи отдела» для home-дашбордов dep_admin/account_admin.
 *
 * Тянет последние worker-task'и тем же live-слоем, что и /worker
 * (`listTasks` без фильтра `created_by` → backend сам режет выдачу по роли:
 * dep_admin/operator/admin видят весь dept-scope). Поллит раз в
 * `TASK_POLL_MS`, клик по строке уводит на карточку задачи `/tasks/{id}`.
 *
 * Список краткий (limit ~15), полную ленту с фильтрами и отменой даёт /worker.
 */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle } from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { formatMsk } from "@/lib/datetime";
import { useUserLabels } from "@/lib/labels";
import { listTasks } from "@/api/server/misc";
import {
  TASK_POLL_MS,
  kindIcon,
  statusBadgeClass,
} from "@/pages/worker/workerLive";
import type { PaginatedList } from "@/api/auth/users";
import type { TaskRead } from "@/api/server/types";

const PREVIEW_COUNT = 15;

// Backend кладёт в task инициатора (created_by), но текущий TaskRead в
// types.ts это поле не описывает. Читаем его узким расширением, не трогая
// общий тип из чужой зоны.
type TaskWithInitiator = TaskRead & { created_by?: string | null };

export function AllTasksWidget() {
  const navigate = useNavigate();

  const tasksQ = useQuery<PaginatedList<TaskRead>>(
    () => listTasks({ limit: PREVIEW_COUNT }),
    [],
    { keepPreviousDataOnError: true },
  );

  // Лёгкий поллинг: useQuery без встроенного интервала, поэтому дёргаем refetch
  // вручную. keepPreviousDataOnError держит список на месте при транзиентном
  // 5xx, чтобы панель не мигала пустотой между опросами.
  const { refetch } = tasksQ;
  useEffect(() => {
    const id = window.setInterval(refetch, TASK_POLL_MS);
    return () => window.clearInterval(id);
  }, [refetch]);

  const items = (tasksQ.data?.items ?? []) as TaskWithInitiator[];
  const userLabel = useUserLabels(items.map((t) => t.created_by));

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold">Задачи отдела</h3>
        <button
          type="button"
          className="text-xs text-accent"
          onClick={() => navigate("/worker")}
        >
          Все задачи →
        </button>
      </div>
      {tasksQ.loading && items.length === 0 ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : tasksQ.error && items.length === 0 ? (
        <div className="alert alert-danger flex items-start gap-2 text-xs">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1">
            <div>{apiErrMsg(tasksQ.error, "Задачи не загрузились")}</div>
            <button className="btn btn-ghost mt-2" onClick={() => refetch()}>
              Повторить
            </button>
          </div>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-card text-xs">Задач в отделе пока нет.</div>
      ) : (
        <div className="text-sm">
          {items.map((task) => {
            const Icon = kindIcon(task.kind);
            return (
              <button
                key={task.id}
                type="button"
                onClick={() => navigate(`/tasks/${task.id}`)}
                className="activity-row w-full text-left hover-bg"
              >
                <Icon className="w-4 h-4 text-dim shrink-0" />
                <div className="min-w-0">
                  <div className="truncate mono">{task.kind}</div>
                  <div
                    className="text-[11px] text-dim mono truncate"
                    title={task.created_by ?? undefined}
                  >
                    {task.created_by ? userLabel(task.created_by) : "—"} ·{" "}
                    {formatMsk(task.created_at)}
                  </div>
                </div>
                <span className={statusBadgeClass(task.status)}>
                  {task.status}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
