/**
 * Страница /worker — live-список worker-task'ей (server_worker) + деталь.
 *
 * Shell + Aside (список с фильтром status/kind, поиском, пагинацией через
 * X-Total-Count) + Workzone (TaskDetail). Список и деталь поллятся каждые ~10с.
 *
 * account_admin / logging_admin отрезаны от server-зоны backend'ом
 * (`GET /tasks` им вернёт 403) — показываем BlockedDetail вместо мёртвой
 * страницы. dep_admin видит задачи серверов своего отдела; server.*-роли — по
 * матрице. Cancel гейтится по ролям (`canCancelTask`).
 */
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
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

export function Worker() {
  const { persona } = usePersona();
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("id");

  const zoneBlocked = isServerZoneBlocked(persona);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");

  const list = useTaskList(
    { status: statusFilter, kind: kindFilter },
    { enabled: !zoneBlocked },
  );

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    setParams(next, { replace: true });
  }

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_worker / tasks">
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
      statusFilter={statusFilter}
      onStatusFilter={setStatusFilter}
      kindFilter={kindFilter}
      onKindFilter={setKindFilter}
      onLoadMore={list.loadMore}
      loadingMore={list.loadingMore}
    />
  );

  return (
    <Shell breadcrumb="server_worker / tasks" middle={aside}>
      {selectedId ? (
        <TaskDetail
          key={selectedId}
          taskId={selectedId}
          canCancel={canCancelTask(persona)}
          onChanged={list.refetch}
        />
      ) : (
        <EmptyDetail />
      )}
    </Shell>
  );
}
