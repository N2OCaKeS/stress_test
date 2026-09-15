/**
 * Страница /worker/dlq — упавшие worker-task'и (DLQ).
 *
 * Та же раскладка, что и /worker, но список зафиксирован на `status=failed`
 * (отдельного DLQ-endpoint'а нет — DLQ = `GET /tasks?status=failed`). Фокус —
 * на `last_error` в детали. Retry-from-DLQ backend пока не поддерживает —
 * показываем disabled-кнопку с пояснением; cancel остаётся доступным, если
 * задача всё ещё cancelable (на практике failed-row терминальна, кнопка
 * скрыта, но gate переиспользуется честно).
 */
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { RotateCcw } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { isServerZoneBlocked } from "@/lib/rbac";
import {
  TaskListAside,
  TaskDetail,
  EmptyDetail,
  BlockedDetail,
  canCancelTask,
  useTaskList,
} from "./workerLive";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";

export function WorkerDlq() {
  const { persona } = usePersona();
  const { notImplemented } = useConfirm();
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("id");

  const zoneBlocked = isServerZoneBlocked(persona);

  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("");

  const list = useTaskList(
    { status: "failed", kind: kindFilter },
    { enabled: !zoneBlocked, fixedStatus: "failed" },
  );

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    setParams(next, { replace: true });
  }

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_worker / dlq">
        <BlockedDetail />
      </Shell>
    );
  }

  const aside = (
    <TaskListAside
      tasks={list.tasks}
      total={list.total}
      loading={list.loading}
      error={list.error}
      selectedId={selectedId}
      onSelect={selectId}
      onRetry={list.refetch}
      search={search}
      onSearch={setSearch}
      statusFilter="failed"
      onStatusFilter={() => {}}
      kindFilter={kindFilter}
      onKindFilter={setKindFilter}
      hideStatusFilter
      onLoadMore={list.loadMore}
      loadingMore={list.loadingMore}
    />
  );

  return (
    <Shell breadcrumb="server_worker / dlq" middle={aside}>
      {selectedId ? (
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden relative">
          <div className="absolute top-5 right-5 z-10">
            <Button
              className="flex items-center gap-1"
              onClick={() => notImplemented({ title: "Retry из DLQ в разработке" })}
            >
              <RotateCcw className="w-4 h-4" /> Retry
            </Button>
          </div>
          <TaskDetail
            key={selectedId}
            taskId={selectedId}
            canCancel={canCancelTask(persona)}
            onChanged={list.refetch}
          />
        </div>
      ) : (
        <EmptyDetail note="Выберите упавшую задачу слева — в детали будет last_error." />
      )}
    </Shell>
  );
}
