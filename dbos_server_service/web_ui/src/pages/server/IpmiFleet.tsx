/**
 * Страница /server/ipmi — fleet-wide список IPMI-контроллеров отдела.
 *
 * Per-server управление BMC живёт на вкладке IPMI карточки сервера; эта
 * страница даёт сводную картину «все контроллеры разом» поверх
 * `GET /api/server/v1/ipmi-controllers`. Строка ведёт на карточку своего
 * сервера (deep-link `/server?id=<server_id>`), откуда доступна вкладка IPMI
 * с регистрацией/ротацией/power-операциями.
 *
 * account_admin / logging_* отрезаны от server-зоны backend'ом
 * (`PLATFORM_ADMIN_BUSINESS_DATA_DENIED`) — показываем BlockedPane вместо
 * мёртвой страницы. dep_admin видит контроллеры серверов своего отдела;
 * server.*-роли — по матрице. Backend сам скоупит выдачу по department'у.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Search,
  Cpu,
  AlertCircle,
  CircleCheck,
  CircleX,
  CircleHelp,
  ChevronRight,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listIpmiControllers } from "@/api/server/ipmi";
import { useServerLabel } from "@/lib/labels";
import { formatMsk } from "@/lib/datetime";
import { isServerZoneBlocked } from "@/lib/rbac";
import type { IpmiController, IpmiKind } from "@/api/server/types";

// Контроллеров на отдел немного — одной страницы с запасом хватает, клиентский
// поиск/сорт идут по загруженному набору. Если упрётся в кап — TruncationNotice
// честно покажет «N из M».
const PAGE_LIMIT = 200;

const KIND_LABEL: Record<IpmiKind, string> = {
  idrac: "iDRAC",
  ilo: "iLO",
  ipmi: "IPMI",
  redfish: "Redfish",
};

// last_status из probe BMC: ok / unreachable / auth_failed. Незнакомое
// значение пробрасываем как есть (?? raw), не теряя сигнал.
const STATUS_BADGE: Record<string, { kind: "ok" | "warn" | "danger"; label: string }> = {
  ok: { kind: "ok", label: "ok" },
  unreachable: { kind: "danger", label: "unreachable" },
  auth_failed: { kind: "warn", label: "auth_failed" },
};

type SortMode = "server" | "kind" | "status";

export function IpmiFleet() {
  const { persona } = usePersona();
  const zoneBlocked = isServerZoneBlocked(persona);

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortMode>("server");

  const listQ = useQuery(
    () => listIpmiControllers({ limit: PAGE_LIMIT }),
    [],
    { enabled: !zoneBlocked },
  );

  const items = listQ.data?.items ?? [];
  const total = listQ.data?.total ?? items.length;

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const matched = items.filter((c) => {
      if (!term) return true;
      return (
        c.server_id.toLowerCase().includes(term) ||
        c.endpoint_url.toLowerCase().includes(term) ||
        c.username.toLowerCase().includes(term) ||
        c.kind.toLowerCase().includes(term) ||
        c.id.toLowerCase().includes(term)
      );
    });
    const sorted = [...matched].sort((a, b) => {
      if (sort === "kind") return a.kind.localeCompare(b.kind);
      if (sort === "status")
        return (a.last_status ?? "").localeCompare(b.last_status ?? "");
      return a.server_id.localeCompare(b.server_id);
    });
    return sorted;
  }, [items, search, sort]);

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_service / ipmi">
        <BlockedPane />
      </Shell>
    );
  }

  return (
    <Shell breadcrumb="server_service / ipmi">
      <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="border-b border-token px-5 py-4 shrink-0">
          <div className="flex items-center gap-3 flex-wrap">
            <Cpu className="w-6 h-6 text-accent shrink-0" />
            <div className="flex-1 min-w-0">
              <h1 className="text-lg font-semibold">IPMI-контроллеры</h1>
              <div className="text-xs text-dim">
                Все BMC серверов отдела. Управление — на вкладке IPMI карточки
                сервера.
              </div>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1 flex-1 min-w-[16rem]">
              <Search className="w-4 h-4 text-dim shrink-0" />
              <input
                className="bg-transparent outline-none flex-1 text-sm"
                placeholder={`Поиск по ${items.length} контроллерам…`}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <label className="text-xs text-dim flex items-center gap-1.5">
              Сорт:
              <select
                className="surface-2 border border-token rounded px-2 py-1"
                value={sort}
                onChange={(e) => setSort(e.target.value as SortMode)}
              >
                <option value="server">по серверу</option>
                <option value="kind">по типу</option>
                <option value="status">по статусу</option>
              </select>
            </label>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-5">
          {listQ.loading && (
            <div className="py-10 text-sm text-dim text-center">Загрузка…</div>
          )}

          {!listQ.loading && listQ.error != null && (
            <div className="alert alert-danger flex items-start gap-2 max-w-xl">
              <AlertCircle className="w-5 h-5 mt-0.5 shrink-0" />
              <div className="flex-1 text-sm">
                <div>{apiErrMsg(listQ.error, "Список контроллеров не загрузился")}</div>
                <button className="btn btn-ghost mt-2" onClick={() => listQ.refetch()}>
                  Повторить
                </button>
              </div>
            </div>
          )}

          {!listQ.loading && listQ.error == null && filtered.length === 0 && (
            <EmptyPane hasAny={items.length > 0} />
          )}

          {!listQ.loading && listQ.error == null && filtered.length > 0 && (
            <div className="flex flex-col gap-1.5 w-full">
              {filtered.map((c) => (
                <ControllerRow key={c.id} controller={c} />
              ))}
            </div>
          )}

          {!listQ.loading && listQ.error == null && (
            <TruncationNotice
              shown={items.length}
              total={total}
              className="mt-3"
            />
          )}
        </div>
      </section>
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string | null }) {
  if (status == null) {
    return (
      <span className="badge" title="Контроллер ещё не опрашивался">
        <CircleHelp className="w-3.5 h-3.5" /> не опрошен
      </span>
    );
  }
  const meta = STATUS_BADGE[status];
  const Icon =
    meta?.kind === "ok" ? CircleCheck : meta?.kind === "danger" ? CircleX : CircleHelp;
  return (
    <span className={`badge${meta ? ` badge-${meta.kind}` : ""}`}>
      <Icon className="w-3.5 h-3.5" />
      {meta?.label ?? status}
    </span>
  );
}

function ControllerRow({ controller }: { controller: IpmiController }) {
  const serverLabel = useServerLabel(controller.server_id);
  const kindLabel = KIND_LABEL[controller.kind] ?? controller.kind;
  return (
    <Link
      to={`/server?id=${encodeURIComponent(controller.server_id)}`}
      className="cred-row text-left"
      title="Открыть карточку сервера (вкладка IPMI)"
    >
      <div className="flex items-center gap-3">
        <Cpu className="w-4 h-4 text-dim shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate">
            <span className="font-medium">{serverLabel}</span>
            <span className="badge ml-2">{kindLabel}</span>
          </div>
          <div className="text-[11px] text-dim flex items-center gap-2 flex-wrap mono">
            <span className="truncate">{controller.endpoint_url}</span>
            <span>·</span>
            <span>{controller.username}</span>
          </div>
        </div>
        <div className="hidden sm:flex flex-col items-end text-[11px] text-dim shrink-0">
          <span>опрос</span>
          <span>{formatMsk(controller.last_probed_at)}</span>
        </div>
        <StatusBadge status={controller.last_status} />
        <ChevronRight className="w-4 h-4 text-dim shrink-0" />
      </div>
    </Link>
  );
}

function EmptyPane({ hasAny }: { hasAny: boolean }) {
  return (
    <div className="empty-card max-w-md text-center mx-auto mt-6">
      <Cpu className="w-10 h-10 mx-auto text-dim mb-3" />
      <div className="text-sm text-dim">
        {hasAny
          ? "Под текущий поиск контроллеров нет."
          : "В отделе нет зарегистрированных IPMI-контроллеров. BMC регистрируется на вкладке IPMI карточки сервера."}
      </div>
    </div>
  );
}

function BlockedPane() {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <AlertCircle className="w-10 h-10 mx-auto text-warn mb-3" />
        <div className="text-sm font-medium mb-2">
          Раздел недоступен для платформенного администратора
        </div>
        <div className="text-xs text-dim">
          server_service отделяет управление платформой от бизнес-данных
          серверов. Учётка <b>account_admin</b> / <b>logging_admin</b> не имеет
          доступа к IPMI-контроллерам — работайте под департаментной ролью
          (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}
