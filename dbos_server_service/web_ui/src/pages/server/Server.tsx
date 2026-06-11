/**
 * Страница /servers — Shell + Aside (список) + Workzone (ServerDetail c табами).
 *
 * Шаблон взят из new_allta_app/frontend/src/components/StandDetail.tsx +
 * ServerList.tsx, адаптирован под нашу систему компонентов
 * (`@/components/shell/Shell`, `@/components/ui/Tabs`, `@/api/server/servers`).
 *
 * Live-страница без mock-режима: списки и detail тянем напрямую через
 * `server_service`.
 */
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Search,
  Server as ServerIcon,
  Plus,
  ArrowLeft,
  AlertCircle,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import {
  createServer,
  deleteServer,
  listServers,
} from "@/api/server/servers";
import { listDepartments } from "@/api/auth/departments";
import { useDeptLabel } from "@/lib/labels";
import { isServerZoneBlocked } from "@/lib/rbac";
import type {
  Server,
  ServerCreateRequest,
  ServerStatus,
} from "@/api/server/types";
import type { Department } from "@/api/auth/types";
import { ServerDetail } from "./ServerDetail";

const STATUS_LABEL: Record<ServerStatus, string> = {
  unknown: "unknown",
  online: "online",
  offline: "offline",
  maintenance: "maint",
  decommissioned: "decom",
};

const STATUS_KIND: Record<ServerStatus, "ok" | "warn" | "danger" | ""> = {
  unknown: "",
  online: "ok",
  offline: "danger",
  maintenance: "warn",
  decommissioned: "",
};

type SortMode = "name" | "dept" | "status";
type GroupMode = "none" | "department";

export function Server() {
  const { persona } = usePersona();
  const toast = useToast();
  const [params, setParams] = useSearchParams();

  const selectedId = params.get("id");
  const action = params.get("action"); // "new" | "edit" | null

  // account_admin / logging_admin отрезаны от server_service на уровне
  // backend-middleware (PLATFORM_ADMIN_BUSINESS_DATA_DENIED) — даже GET-список
  // им вернёт 403. Не дёргаем API и сразу показываем объяснение вместо
  // мёртвой страницы с кнопками, которые всё равно отобьются 403.
  const zoneBlocked = isServerZoneBlocked(persona);

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortMode>("name");
  const [group, setGroup] = useState<GroupMode>("none");
  const [filterDept, setFilterDept] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterBusy, setFilterBusy] = useState<string>("");

  const listQ = useQuery(
    () =>
      listServers({
        limit: 200,
        department_id: filterDept || undefined,
        status: filterStatus || undefined,
        busy: filterBusy || undefined,
      }),
    [filterDept, filterStatus, filterBusy],
    { enabled: !zoneBlocked },
  );
  const depsQ = useQuery<Department[]>(() => listDepartments(), []);

  const canManage =
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";

  const items = listQ.data?.items ?? [];
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const matched = term
      ? items.filter((s) => {
          return (
            s.hostname.toLowerCase().includes(term) ||
            (s.display_name?.toLowerCase().includes(term) ?? false) ||
            s.ip_address.toLowerCase().includes(term) ||
            s.id.toLowerCase().includes(term)
          );
        })
      : items;
    const sorted = [...matched].sort((a, b) => {
      if (sort === "name")
        return (a.display_name ?? a.hostname).localeCompare(
          b.display_name ?? b.hostname,
        );
      if (sort === "dept") return a.department_id.localeCompare(b.department_id);
      if (sort === "status") return a.status.localeCompare(b.status);
      return 0;
    });
    return sorted;
  }, [items, search, sort]);

  const grouped = useMemo(() => {
    if (group !== "department") return [{ key: "all", items: filtered }];
    const map = new Map<string, Server[]>();
    for (const s of filtered) {
      const k = s.department_id;
      const arr = map.get(k) ?? [];
      arr.push(s);
      map.set(k, arr);
    }
    return Array.from(map.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, items]) => ({ key, items }));
  }, [filtered, group]);

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function startCreate() {
    const next = new URLSearchParams(params);
    next.set("action", "new");
    next.delete("id");
    setParams(next, { replace: true });
  }
  function closeAction() {
    const next = new URLSearchParams(params);
    next.delete("action");
    setParams(next, { replace: true });
  }

  async function handleDelete(server: Server) {
    if (typeof window === "undefined") return;
    const reason = window.prompt(
      `Причина удаления "${server.hostname}":`,
      "",
    );
    if (reason === null) return;
    if (!reason.trim()) {
      toast.warn("Причина обязательна");
      return;
    }
    const confirmed = window.confirm(
      `Удалить сервер ${server.hostname}? Операция необратима.`,
    );
    if (!confirmed) return;
    try {
      await deleteServer(server.id, { reason: reason.trim() });
      toast.success(`Сервер ${server.hostname} удалён`);
      selectId(null);
      listQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    }
  }

  async function handleCreate(body: ServerCreateRequest) {
    try {
      const created = await createServer(body);
      toast.success(`Сервер ${created.hostname} создан`);
      listQ.refetch();
      const next = new URLSearchParams(params);
      next.set("id", created.id);
      next.delete("action");
      setParams(next, { replace: true });
    } catch (e) {
      toast.error(apiErrMsg(e, "Создание не удалось"));
    }
  }

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${items.length} серверам…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
          <span>Сорт:</span>
          <select
            className="surface-2 border border-token rounded px-2 py-0.5"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortMode)}
          >
            <option value="name">по имени</option>
            <option value="dept">по отделу</option>
            <option value="status">по статусу</option>
          </select>
          <span>Группа:</span>
          <select
            className="surface-2 border border-token rounded px-2 py-0.5"
            value={group}
            onChange={(e) => setGroup(e.target.value as GroupMode)}
          >
            <option value="none">—</option>
            <option value="department">по отделу</option>
          </select>
        </div>
        <FilterPane
          depts={depsQ.data ?? []}
          dept={filterDept}
          status={filterStatus}
          busy={filterBusy}
          onDept={setFilterDept}
          onStatus={setFilterStatus}
          onBusy={setFilterBusy}
        />
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {listQ.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {listQ.error && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(listQ.error, "Список не загрузился")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => listQ.refetch()}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
        {!listQ.loading && !listQ.error && filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            Список пуст.
          </div>
        )}
        {grouped.map((bucket) => (
          <ServerGroup
            key={bucket.key}
            groupKey={bucket.key}
            showHeader={group === "department"}
            items={bucket.items}
            selectedId={selectedId}
            onSelect={selectId}
          />
        ))}
      </div>

      {canManage && (
        <div className="border-t border-token p-3 shrink-0">
          <button
            className="btn btn-primary w-full flex items-center justify-center gap-2"
            onClick={startCreate}
          >
            <Plus className="w-4 h-4" /> Создать сервер
          </button>
        </div>
      )}
    </aside>
  );

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_service / servers">
        <BlockedPane />
      </Shell>
    );
  }

  return (
    <Shell breadcrumb="server_service / servers" middle={aside}>
      {action === "new" && canManage ? (
        <CreatePane
          depts={depsQ.data ?? []}
          onCancel={closeAction}
          onSubmit={handleCreate}
        />
      ) : selectedId ? (
        <WorkzoneWithActions
          serverId={selectedId}
          servers={items}
          onDelete={handleDelete}
          canManage={canManage}
        />
      ) : (
        <EmptyPane canCreate={canManage} onCreate={startCreate} />
      )}
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function FilterPane({
  depts,
  dept,
  status,
  busy,
  onDept,
  onStatus,
  onBusy,
}: {
  depts: Department[];
  dept: string;
  status: string;
  busy: string;
  onDept: (v: string) => void;
  onStatus: (v: string) => void;
  onBusy: (v: string) => void;
}) {
  return (
    <div className="mt-2 grid grid-cols-3 gap-1 text-[11px] text-dim">
      <select
        className="surface-2 border border-token rounded px-1 py-0.5"
        value={dept}
        onChange={(e) => onDept(e.target.value)}
        title="Фильтр по департаменту"
      >
        <option value="">все отделы</option>
        {depts.map((d) => (
          <option key={d.id} value={d.id}>
            {d.name}
          </option>
        ))}
      </select>
      <select
        className="surface-2 border border-token rounded px-1 py-0.5"
        value={status}
        onChange={(e) => onStatus(e.target.value)}
        title="Фильтр по статусу"
      >
        <option value="">все статусы</option>
        <option value="online">online</option>
        <option value="offline">offline</option>
        <option value="maintenance">maintenance</option>
        <option value="decommissioned">decommissioned</option>
        <option value="unknown">unknown</option>
      </select>
      <select
        className="surface-2 border border-token rounded px-1 py-0.5"
        value={busy}
        onChange={(e) => onBusy(e.target.value)}
        title="Фильтр по занятости"
      >
        <option value="">все</option>
        <option value="free">свободные</option>
        <option value="busy">занятые</option>
        <option value="testing">в тесте</option>
      </select>
    </div>
  );
}

function ServerGroup({
  groupKey,
  showHeader,
  items,
  selectedId,
  onSelect,
}: {
  groupKey: string;
  showHeader: boolean;
  items: Server[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const deptLabel = useDeptLabel(showHeader ? groupKey : null);
  return (
    <div>
      {showHeader && (
        <div className="group-header px-3 mt-2 text-[11px] uppercase text-dim">
          {deptLabel} · {items.length}
        </div>
      )}
      <div className="px-2 flex flex-col gap-0.5">
        {items.map((s) => (
          <ServerRow
            key={s.id}
            server={s}
            active={selectedId === s.id}
            onSelect={() => onSelect(s.id)}
          />
        ))}
      </div>
    </div>
  );
}

function ServerRow({
  server,
  active,
  onSelect,
}: {
  server: Server;
  active: boolean;
  onSelect: () => void;
}) {
  const deptLabel = useDeptLabel(server.department_id);
  const statusKind = STATUS_KIND[server.status];
  const busyChipKind: "ok" | "warn" = server.busy_state === "free" ? "ok" : "warn";
  const busyChipLabel =
    server.busy_state === "free"
      ? "free"
      : server.busy_state === "testing"
        ? "test"
        : "busy";
  const name = server.display_name ?? server.hostname;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left ${active ? "active" : ""}`}
    >
      <div className="flex items-center gap-2">
        <ServerIcon
          className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`}
        />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate">{name}</div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <span className="truncate">{deptLabel}</span>
            <span>·</span>
            <span className="mono">{server.ip_address}</span>
          </div>
        </div>
        <span className={`badge${statusKind ? ` badge-${statusKind}` : ""}`}>
          {STATUS_LABEL[server.status]}
        </span>
        <span className={`badge badge-${busyChipKind}`}>{busyChipLabel}</span>
      </div>
    </button>
  );
}

// ───────────────────────────────────────────────────────────────────────────

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
          доступа к серверам и аккаунтам — работайте под департаментной ролью
          (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}

function EmptyPane({
  canCreate,
  onCreate,
}: {
  canCreate: boolean;
  onCreate: () => void;
}) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <ServerIcon className="w-10 h-10 mx-auto text-dim mb-3" />
        <div className="text-sm text-dim mb-3">
          Выберите сервер слева для просмотра деталей.
        </div>
        {canCreate && (
          <button
            className="btn btn-primary inline-flex items-center gap-1"
            onClick={onCreate}
          >
            <Plus className="w-4 h-4" /> Создать сервер
          </button>
        )}
      </div>
    </section>
  );
}

function WorkzoneWithActions({
  serverId,
  servers,
  onDelete,
  canManage,
}: {
  serverId: string;
  servers: Server[];
  onDelete: (s: Server) => void;
  canManage: boolean;
}) {
  const local = servers.find((s) => s.id === serverId);
  return (
    <div className="flex-1 min-w-0 flex flex-col overflow-hidden relative">
      {canManage && local && (
        <div className="absolute top-3 right-5 z-10">
          <button
            className="btn btn-danger"
            onClick={() => onDelete(local)}
            title="Удалить сервер"
          >
            Удалить
          </button>
        </div>
      )}
      <ServerDetail serverId={serverId} />
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

// Грубая структурная проверка IPv4/IPv6 — ровно чтобы отсечь явный мусор до
// отправки. Каноникализацию и финальную валидацию делает backend (INET).
function isLikelyIpAddress(value: string): boolean {
  const v = value.trim();
  const ipv4 =
    /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
  if (ipv4.test(v)) return true;
  // IPv6: hex-группы и `::`-сжатие; достаточно для отсечения непохожего ввода.
  return v.includes(":") && /^[0-9a-fA-F:]+$/.test(v) && v.length >= 2;
}

function CreatePane({
  depts,
  onCancel,
  onSubmit,
}: {
  depts: Department[];
  onCancel: () => void;
  onSubmit: (body: ServerCreateRequest) => void | Promise<void>;
}) {
  const [hostname, setHostname] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [departmentId, setDepartmentId] = useState(depts[0]?.id ?? "");
  const [ipAddress, setIpAddress] = useState("");
  const [sshPort, setSshPort] = useState<string>("22");
  const [submitting, setSubmitting] = useState(false);

  // backend кладёт ip_address в INET (IPv4Address | IPv6Address) и отбивает
  // мусор 422 ещё на pydantic; гасим заведомо-битый ввод заранее.
  const ipError =
    ipAddress.trim() && !isLikelyIpAddress(ipAddress.trim())
      ? "Ожидается IPv4 или IPv6 адрес"
      : null;

  // depts может прийти позже — подхватим первый, если ещё не выбран.
  if (!departmentId && depts.length) {
    setDepartmentId(depts[0].id);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    if (!hostname.trim() || !ipAddress.trim() || !departmentId || ipError) return;
    const body: ServerCreateRequest = {
      hostname: hostname.trim(),
      display_name: displayName.trim() || null,
      ip_address: ipAddress.trim(),
      department_id: departmentId,
      ssh_port: Number(sshPort) || 22,
    };
    setSubmitting(true);
    Promise.resolve(onSubmit(body)).finally(() => setSubmitting(false));
  }

  return (
    <section className="flex-1 min-w-0 overflow-y-auto">
      <div className="p-5 max-w-xl">
        <div className="flex items-center gap-2 mb-4">
          <button
            className="btn btn-ghost flex items-center gap-1"
            onClick={onCancel}
            type="button"
          >
            <ArrowLeft className="w-4 h-4" /> Назад
          </button>
          <div className="text-sm text-dim">Создание сервера</div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">hostname *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              required
              maxLength={255}
              placeholder="srv-node-01"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">display name</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="опционально"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">department *</span>
            <select
              className="surface-2 border border-token rounded px-2 py-1"
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
              required
            >
              {depts.length === 0 && <option value="">— нет отделов —</option>}
              {depts.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">IP address *</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={ipAddress}
              onChange={(e) => setIpAddress(e.target.value)}
              required
              placeholder="10.10.20.11"
            />
            {ipError && (
              <span className="text-[11px] text-danger">{ipError}</span>
            )}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">SSH port</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              type="number"
              min={1}
              max={65535}
              value={sshPort}
              onChange={(e) => setSshPort(e.target.value)}
            />
          </label>

          <div className="flex items-center gap-2 mt-2">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={
                submitting ||
                !hostname.trim() ||
                !ipAddress.trim() ||
                !departmentId ||
                !!ipError
              }
            >
              {submitting ? "Создаём…" : "Создать"}
            </button>
            <button type="button" className="btn" onClick={onCancel}>
              Отмена
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
