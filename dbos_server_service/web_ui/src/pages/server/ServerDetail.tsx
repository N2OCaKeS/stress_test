/**
 * Карточка сервера справа от Aside-листа — тонкая обёртка над общей рабочей
 * зоной `EntityDetail`. Здесь только загрузка сервера (`getServer`) и локальная
 * копия карточки: мутации во вкладках и бронь поднимаются сюда через
 * `onLocalUpdate`, чтобы шапка и соседние вкладки увидели свежий объект без
 * перезагрузки. Всё остальное — шапка, бронь, полоса вкладок, рендер вкладок —
 * живёт в `EntityDetail`.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@/api/auth/useQuery";
import { getServer } from "@/api/server/servers";
import { EntityDetail } from "@/components/entity/EntityDetail";
import { ApiError } from "@/api/client";
import type { Server } from "@/api/server/types";
import type { EntityRef } from "@/pages/server/tabs/_entity";

interface ServerDetailProps {
  serverId: string;
  onDeleted?: () => void;
  /** Вызывается после захвата/снятия брони — чтобы список слева обновил индикатор. */
  onBusyChanged?: () => void;
}

const DETAIL_REFRESH_MS = 3_000;

export function ServerDetail({
  serverId,
  onDeleted,
  onBusyChanged,
}: ServerDetailProps) {
  const q = useQuery<Server>(() => getServer(serverId), [serverId]);
  const refetch = q.refetch;
  const refetchRef = useRef(refetch);
  refetchRef.current = refetch;

  // Локальная копия карточки: переключение вкладок не перемонтирует обёртку,
  // поэтому мутации внутри вкладок (PATCH overview/hardware, бронь) поднимаются
  // сюда через onLocalUpdate. Сидируется из query, обновляется при refetch.
  const [server, setServer] = useState<Server | undefined>(q.data);
  useEffect(() => {
    setServer(q.data);
  }, [q.data]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") refetchRef.current();
    }, DETAIL_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [serverId]);

  const handleChanged = useCallback(() => {
    refetch();
    onBusyChanged?.();
  }, [refetch, onBusyChanged]);

  if (q.loading) {
    return (
      <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="p-8 text-sm text-dim">Загружаем сервер…</div>
      </section>
    );
  }
  if (q.error || !q.data) {
    const msg =
      q.error instanceof ApiError
        ? `${q.error.errorCode}: ${q.error.message}`
        : q.error?.message ?? "Сервер не найден";
    return (
      <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="m-5 alert alert-danger flex items-center gap-3">
          <span className="text-sm">{msg}</span>
          <button className="btn btn-ghost" onClick={() => q.refetch()}>
            Повторить
          </button>
        </div>
      </section>
    );
  }

  const current = server ?? q.data;
  const entity: EntityRef = { kind: "server", server: current };
  return (
    <EntityDetail
      entity={entity}
      onLocalUpdate={(next) => setServer(next as Server)}
      onDeleted={onDeleted}
      onBusyChanged={onBusyChanged}
      onChanged={handleChanged}
    />
  );
}
