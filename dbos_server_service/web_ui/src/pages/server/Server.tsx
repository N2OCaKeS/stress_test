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
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Search,
  Server as ServerIcon,
  Plus,
  ArrowLeft,
  AlertCircle,
  Play,
  ListChecks,
  MonitorPlay,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery, useMockMode } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listVms, type Vm } from "@/api/server/vms";
import { MOCK_VMS } from "@/mocks/vm";
import {
  createServer,
  deleteServer,
  listServers,
} from "@/api/server/servers";
import {
  formatLatencyMs,
  reservedErrorMessage,
} from "@/pages/server/_serverShared";
import { BulkPrepareModal } from "@/pages/server/_bulkPrepareModal";
import { listDepartments } from "@/api/auth/departments";
import { useDeptLabel } from "@/lib/labels";
import {
  canManageVms,
  isPlatformWideAdmin,
  isServerZoneBlocked,
} from "@/lib/rbac";
import type { Server, ServerCreateRequest } from "@/api/server/types";
import type { Department } from "@/api/auth/types";
import { ServerDetail } from "./ServerDetail";
import { VmDetail } from "@/pages/vm/Vm";

const FOCUS_REFETCH_THROTTLE_MS = 12_000;

type SortMode = "name" | "dept" | "status";
type GroupMode = "none" | "department";

export function Server() {
  const { persona } = usePersona();
  const toast = useToast();
  const { prompt } = useConfirm();
  const [params, setParams] = useSearchParams();

  const selectedId = params.get("id");
  // Выбранная ВМ открывается прямо в этой странице (рабочая область справа),
  // средняя панель остаётся списком ВМ. Не уходим на /vm — там хаб-центричная
  // раскладка, которая подменяла бы список одним хабом.
  const selectedVmId = params.get("vm");
  const action = params.get("action"); // "new" | "edit" | null
  // Фильтр состава списка из левой навигации: подпункт «Серверы» → only=servers,
  // «ВМ» → only=vms. Пустое значение (пункт «Серверы») — общий смешанный список.
  const only = params.get("only"); // "servers" | "vms" | null
  const showServers = only !== "vms";
  const showVms = only !== "servers";

  // account_admin / logging_admin отрезаны от server_service на уровне
  // backend-middleware (PLATFORM_ADMIN_BUSINESS_DATA_DENIED) — даже GET-список
  // им вернёт 403. Не дёргаем API и сразу показываем объяснение вместо
  // мёртвой страницы с кнопками, которые всё равно отобьются 403.
  const zoneBlocked = isServerZoneBlocked(persona);
  // Список отделов из auth_service отдаётся только account_admin'у; остальным
  // (dep_admin, server.*-роли) GET /departments вернёт 403. Не дёргаем его для
  // них — иначе 403 сыпется в консоль/тосты, — а отдел создаваемого сервера
  // берём из их персоны (см. CreatePane).
  const isAccountAdmin = isPlatformWideAdmin(persona);

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortMode>("name");
  const [group, setGroup] = useState<GroupMode>("none");
  const [filterDept, setFilterDept] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterBusy, setFilterBusy] = useState<string>("");
  // Мультивыбор серверов для bulk-операций (массовый prepare). Чекбоксы и
  // панель действий видны только в явном «режиме выбора» — иначе список
  // остаётся обычным навигационным, а массовый prepare было не найти.
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkPrepareOpen, setBulkPrepareOpen] = useState(false);

  // server_service возвращает серверы своего отдела (изоляция по identity) и
  // не принимает dept/status/busy как query-фильтры — поэтому тянем страницу
  // один раз, а отдел/статус/занятость фильтруем по загруженному набору ниже.
  const listQ = useQuery(
    () => listServers({ limit: 200 }),
    [],
    { enabled: !zoneBlocked },
  );
  const depsQ = useQuery<Department[]>(() => listDepartments(), [], {
    enabled: isAccountAdmin,
  });

  // ВМ в общем списке: отдельная свёрнутая группа после серверов. Домен `vm`
  // в server_service; backend в работе — в mock-режиме берём фикстуры, в live
  // тихо деградируем (keepPreviousDataOnError), если маршрут ещё не готов.
  const mock = useMockMode();
  const vmsQ = useQuery(
    () =>
      mock
        ? Promise.resolve({
            items: MOCK_VMS,
            total: MOCK_VMS.length,
            limit: 500,
            offset: 0,
          })
        : listVms({ limit: 500 }),
    [mock],
    { enabled: !zoneBlocked, keepPreviousDataOnError: true },
  );
  // Обе группы общего списка сворачиваемые; по умолчанию развёрнуты.
  const [serverGroupOpen, setServerGroupOpen] = useState(true);
  const [vmGroupOpen, setVmGroupOpen] = useState(true);

  // Бронь сервера (busy_state / busy_user_id) меняется и другими пользователями,
  // а useQuery без авто-рефетча показывал бы устаревший индикатор до перезахода.
  // Тихо переопрашиваем список на интервале и при возврате фокуса на вкладку,
  // чтобы чужой захват/освобождение подхватывались быстро. refetch стабилен
  // (useCallback в хуке), поэтому держим его в ref и не пересоздаём интервал.
  const refetchRef = useRef(listQ.refetch);
  refetchRef.current = listQ.refetch;
  const lastRefetchRef = useRef(0);
  useEffect(() => {
    if (zoneBlocked) return;
    const RESERVE_REFRESH_MS = 8_000;
    const id = window.setInterval(() => {
      // На скрытой вкладке не дёргаем сеть — фокус-хендлер ниже догонит при
      // возврате.
      if (document.visibilityState === "visible") refetchRef.current();
    }, RESERVE_REFRESH_MS);
    const onFocus = () => {
      const now = Date.now();
      if (now - lastRefetchRef.current < FOCUS_REFETCH_THROTTLE_MS) return;
      lastRefetchRef.current = now;
      refetchRef.current();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [zoneBlocked]);

  const canManage =
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";
  const canManageVm = canManageVms(persona);

  const items = useMemo(() => listQ.data?.items ?? [], [listQ.data]);
  // `total` — серверная истина (до клиентского поиска): если она больше, чем
  // влезло в страницу (limit:200), показываем баннер усечения.
  const serverTotal = listQ.data?.total ?? items.length;
  // Сорт/группа/поиск работают по загруженному набору. Если серверов больше,
  // чем влезло, «по имени» — порядок среди показанных, а не всех.
  const sortScopeTruncated = items.length < serverTotal;
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const matched = items.filter((s) => {
      if (filterDept && s.department_id !== filterDept) return false;
      if (filterStatus && s.status !== filterStatus) return false;
      if (filterBusy && s.busy_state !== filterBusy) return false;
      if (term) {
        return (
          s.hostname.toLowerCase().includes(term) ||
          (s.display_name?.toLowerCase().includes(term) ?? false) ||
          s.ip_address.toLowerCase().includes(term) ||
          s.id.toLowerCase().includes(term)
        );
      }
      return true;
    });
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
  }, [items, search, sort, filterDept, filterStatus, filterBusy]);

  const vmItems = useMemo(() => vmsQ.data?.items ?? [], [vmsQ.data]);
  const filteredVms = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return vmItems;
    return vmItems.filter(
      (v) =>
        v.name.toLowerCase().includes(term) ||
        (v.ip_address?.toLowerCase().includes(term) ?? false) ||
        String(v.number ?? "").includes(term) ||
        v.id.toLowerCase().includes(term),
    );
  }, [vmItems, search]);

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

  const selectedVm = useMemo(
    () => (selectedVmId ? vmItems.find((v) => v.id === selectedVmId) ?? null : null),
    [vmItems, selectedVmId],
  );

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    next.delete("vm");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function openVm(id: string) {
    const next = new URLSearchParams(params);
    next.set("vm", id);
    next.delete("id");
    next.delete("action");
    setParams(next, { replace: true });
  }
  function closeVm() {
    const next = new URLSearchParams(params);
    next.delete("vm");
    setParams(next, { replace: true });
  }
  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function toggleSelectMode() {
    setSelectMode((on) => {
      if (on) setSelected(new Set());
      return !on;
    });
  }
  const allFilteredSelected =
    filtered.length > 0 && filtered.every((s) => selected.has(s.id));
  function toggleAllFiltered() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        for (const s of filtered) next.delete(s.id);
      } else {
        for (const s of filtered) next.add(s.id);
      }
      return next;
    });
  }
  const selectedServers = useMemo(
    () => items.filter((s) => selected.has(s.id)),
    [items, selected],
  );
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
    const { ok, reason } = await prompt({
      title: "Удалить сервер",
      message: `Удалить сервер ${server.hostname}? Операция необратима.`,
      reason: true,
      reasonLabel: "Причина удаления",
      reasonRequired: true,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteServer(server.id, { reason: reason.trim() });
      toast.success(`Сервер ${server.hostname} удалён`);
      selectId(null);
      listQ.refetch();
    } catch (e) {
      toast.error(reservedErrorMessage(e, "Удаление не удалось"));
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
        {sortScopeTruncated && (
          <div className="mt-2 alert-warn text-[11px]" role="status">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>
              Сортировка и группировка применены к загруженным {items.length}{" "}
              из {serverTotal} серверов — уточните фильтр, чтобы упорядочить
              остальные.
            </span>
          </div>
        )}
        <FilterPane
          depts={depsQ.data ?? []}
          dept={filterDept}
          status={filterStatus}
          busy={filterBusy}
          onDept={setFilterDept}
          onStatus={setFilterStatus}
          onBusy={setFilterBusy}
        />
        {canManage && (
          <div className="mt-2 flex flex-col gap-2">
            <button
              type="button"
              className={`btn btn-sm w-full flex items-center justify-center gap-2 ${
                selectMode ? "btn-primary" : ""
              }`}
              onClick={toggleSelectMode}
              title="Выбрать несколько серверов и подготовить их разом"
            >
              <ListChecks className="w-4 h-4" />
              {selectMode ? "Выйти из режима выбора" : "Массовая подготовка"}
            </button>
            {selectMode && (
              <div className="flex items-center justify-between text-[11px] text-dim">
                <span>Выбрано: {selected.size}</span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={toggleAllFiltered}
                  disabled={filtered.length === 0}
                >
                  {allFilteredSelected ? "Снять все" : "Выбрать все"}
                </button>
              </div>
            )}
          </div>
        )}
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
        {!listQ.loading &&
          !listQ.error &&
          (!showServers || filtered.length === 0) &&
          (!showVms || filteredVms.length === 0) && (
            <div className="px-3 py-6 text-xs text-dim text-center">
              Список пуст.
            </div>
          )}
        {/* Общий список: сервера и ВМ отдельными группами. При dept-группировке
            у серверов свои заголовки по отделам, поэтому сворачиваемую шапку не
            навешиваем — она осталась бы поверх нескольких блоков. */}
        {showServers &&
          (group === "department"
            ? grouped.map((bucket) => (
                <ServerGroup
                  key={bucket.key}
                  groupKey={bucket.key}
                  showHeader
                  items={bucket.items}
                  selectedId={selectedId}
                  onSelect={selectId}
                  selectable={canManage && selectMode}
                  checkedIds={selected}
                  onToggleChecked={toggleSelected}
                />
              ))
            : filtered.length > 0 && (
                <ServerSection
                  count={filtered.length}
                  open={serverGroupOpen}
                  onToggle={() => setServerGroupOpen((v) => !v)}
                  items={filtered}
                  selectedId={selectedId}
                  onSelect={selectId}
                  selectable={canManage && selectMode}
                  checkedIds={selected}
                  onToggleChecked={toggleSelected}
                />
              ))}
        {showVms && filteredVms.length > 0 && (
          <VmGroup
            vms={filteredVms}
            open={vmGroupOpen}
            onToggle={() => setVmGroupOpen((v) => !v)}
            selectedVmId={selectedVmId}
            onOpenVm={(vm) => openVm(vm.id)}
          />
        )}
        {!listQ.loading && !listQ.error && (
          <TruncationNotice
            shown={items.length}
            total={serverTotal}
            className="mx-3 mt-2"
          />
        )}
      </div>

      {canManage && (
        <div className="border-t border-token p-3 shrink-0 flex flex-col gap-2">
          {selectMode && (
            <button
              className="btn w-full flex items-center justify-center gap-2"
              onClick={() => setBulkPrepareOpen(true)}
              disabled={selected.size === 0}
              title="Массовый prepare выбранных серверов"
            >
              <Play className="w-4 h-4" /> Подготовить выбранные ({selected.size})
            </button>
          )}
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
          isAccountAdmin={isAccountAdmin}
          fixedDeptId={persona.dept_id}
          onCancel={closeAction}
          onSubmit={handleCreate}
        />
      ) : selectedVm ? (
        <VmDetail
          vm={selectedVm}
          mock={mock}
          canManage={canManageVm}
          onBack={closeVm}
          onChanged={() => vmsQ.refetch()}
        />
      ) : selectedId ? (
        <WorkzoneWithActions
          serverId={selectedId}
          servers={items}
          onDelete={handleDelete}
          onDeleted={() => {
            selectId(null);
            listQ.refetch();
          }}
          onBusyChanged={() => listQ.refetch()}
          canManage={canManage}
        />
      ) : (
        <EmptyPane canCreate={canManage} onCreate={startCreate} />
      )}

      {bulkPrepareOpen && selectedServers.length > 0 && (
        <BulkPrepareModal
          servers={selectedServers}
          onClose={() => setBulkPrepareOpen(false)}
          onDone={() => {
            setSelected(new Set());
            listQ.refetch();
          }}
        />
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
  selectable,
  checkedIds,
  onToggleChecked,
}: {
  groupKey: string;
  showHeader: boolean;
  items: Server[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  selectable: boolean;
  checkedIds: Set<string>;
  onToggleChecked: (id: string) => void;
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
            selectable={selectable}
            checked={checkedIds.has(s.id)}
            onToggleChecked={() => onToggleChecked(s.id)}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * Свёрнутая группа серверов в общем списке: тумблер-заголовок со счётчиком и
 * плоский список строк. Используется вне dept-группировки — там заголовки идут
 * по отделам через ServerGroup.
 */
function ServerSection({
  count,
  open,
  onToggle,
  items,
  selectedId,
  onSelect,
  selectable,
  checkedIds,
  onToggleChecked,
}: {
  count: number;
  open: boolean;
  onToggle: () => void;
  items: Server[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  selectable: boolean;
  checkedIds: Set<string>;
  onToggleChecked: (id: string) => void;
}) {
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-1 px-3 py-1 text-[11px] uppercase text-dim hover-bg"
      >
        {open ? (
          <ChevronDown className="w-3.5 h-3.5" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5" />
        )}
        <ServerIcon className="w-3.5 h-3.5" />
        <span>Серверы · {count}</span>
      </button>
      {open && (
        <div className="px-2 flex flex-col gap-0.5 mt-1">
          {items.map((s) => (
            <ServerRow
              key={s.id}
              server={s}
              active={selectedId === s.id}
              onSelect={() => onSelect(s.id)}
              selectable={selectable}
              checked={checkedIds.has(s.id)}
              onToggleChecked={() => onToggleChecked(s.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ServerRow({
  server,
  active,
  onSelect,
  selectable,
  checked,
  onToggleChecked,
}: {
  server: Server;
  active: boolean;
  onSelect: () => void;
  selectable: boolean;
  checked: boolean;
  onToggleChecked: () => void;
}) {
  const deptLabel = useDeptLabel(server.department_id);
  const busyChipKind: "ok" | "warn" = server.busy_state === "free" ? "ok" : "warn";
  const busyChipLabel =
    server.busy_state === "free"
      ? "free"
      : server.busy_state === "testing"
        ? "test"
        : "busy";
  const name = server.display_name ?? server.hostname;
  return (
    <div className={`cred-row text-left flex items-center gap-2 ${active ? "active" : ""}`}>
      {selectable && (
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggleChecked}
          onClick={(e) => e.stopPropagation()}
          className="shrink-0"
          title="Выбрать для массовой операции"
        />
      )}
      <button
        type="button"
        onClick={onSelect}
        className="flex-1 min-w-0 text-left"
      >
        <div className="flex items-center gap-2">
        <ServerIcon
          className={`w-4 h-4 shrink-0 ${active ? "text-accent" : "text-dim"}`}
        />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate">{name}</div>
          <div className="text-[11px] text-dim flex items-center gap-1.5 min-w-0">
            <span className="uppercase tracking-wide text-[10px] shrink-0">
              сервер
            </span>
            <span className="shrink-0">·</span>
            <span className="truncate">{deptLabel}</span>
            <span className="shrink-0">·</span>
            <span className="mono truncate">{server.ip_address}</span>
          </div>
        </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <PingBadge server={server} />
            <span className={`badge badge-${busyChipKind}`}>{busyChipLabel}</span>
          </div>
        </div>
      </button>
    </div>
  );
}

/**
 * Сворачиваемая группа ВМ в общем списке серверов. Клик по строке уводит в
 * карточку ВМ (VmDetail) в рабочей области этой же страницы, сохраняя список ВМ
 * в средней панели: ВМ — отдельная сущность со своим набором операций, поэтому
 * это не ServerDetail.
 */
function VmGroup({
  vms,
  open,
  onToggle,
  selectedVmId,
  onOpenVm,
}: {
  vms: Vm[];
  open: boolean;
  onToggle: () => void;
  selectedVmId: string | null;
  onOpenVm: (vm: Vm) => void;
}) {
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-1 px-3 py-1 text-[11px] uppercase text-dim hover-bg"
      >
        {open ? (
          <ChevronDown className="w-3.5 h-3.5" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5" />
        )}
        <MonitorPlay className="w-3.5 h-3.5" />
        <span>Виртуальные машины · {vms.length}</span>
      </button>
      {open && (
        <div className="px-2 flex flex-col gap-0.5 mt-1">
          {vms.map((v) => (
            <VmRow
              key={v.id}
              vm={v}
              active={selectedVmId === v.id}
              onOpen={() => onOpenVm(v)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function VmRow({
  vm,
  active,
  onOpen,
}: {
  vm: Vm;
  active: boolean;
  onOpen: () => void;
}) {
  const deptLabel = useDeptLabel(vm.department_id);
  const powerKind =
    vm.power_state === "on" ? "ok" : vm.power_state === "off" ? "danger" : "";
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`cred-row text-left flex items-center gap-2 ${active ? "active" : ""}`}
    >
      <MonitorPlay
        className={`w-4 h-4 shrink-0 ${active ? "text-accent" : "text-dim"}`}
      />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">{vm.name}</div>
        <div className="text-[11px] text-dim flex items-center gap-1.5 min-w-0">
          <span className="uppercase tracking-wide text-[10px] shrink-0">ВМ</span>
          <span className="shrink-0">·</span>
          <span className="truncate">{deptLabel}</span>
          <span className="shrink-0">·</span>
          <span className="mono truncate">{vm.ip_address ?? "—"}</span>
        </div>
      </div>
      <span
        className={`badge shrink-0${powerKind ? ` badge-${powerKind}` : ""}`}
      >
        {vm.power_state}
      </span>
    </button>
  );
}

/**
 * Индикатор доступности сервера по ping в строке списка. Три состояния:
 * доступен (зелёный, latency где есть), недоступен (красный), проба не
 * снималась (нейтральный «—»). ssh/ipmi в списке не показываем — только в
 * деталях; здесь важен один сигнал «жив ли бокс».
 */
function PingBadge({ server }: { server: Server }) {
  const reachable = server.ping_reachable;
  if (reachable == null) {
    return (
      <span className="badge" title="ping: не проверялось">
        —
      </span>
    );
  }
  if (reachable) {
    const lat = formatLatencyMs(server.ping_latency_ms);
    return (
      <span className="badge badge-ok" title="ping: доступен">
        {lat ?? "доступен"}
      </span>
    );
  }
  return (
    <span className="badge badge-danger" title="ping: недоступен">
      недоступен
    </span>
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
  onDeleted,
  onBusyChanged,
  canManage,
}: {
  serverId: string;
  servers: Server[];
  onDelete: (s: Server) => void;
  onDeleted: () => void;
  onBusyChanged: () => void;
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
      <ServerDetail
        serverId={serverId}
        onDeleted={onDeleted}
        onBusyChanged={onBusyChanged}
      />
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
  isAccountAdmin,
  fixedDeptId,
  onCancel,
  onSubmit,
}: {
  depts: Department[];
  isAccountAdmin: boolean;
  fixedDeptId: string | null;
  onCancel: () => void;
  onSubmit: (body: ServerCreateRequest) => void | Promise<void>;
}) {
  const [hostname, setHostname] = useState("");
  const [displayName, setDisplayName] = useState("");
  // account_admin выбирает отдел из выпадающего списка (listDepartments ему
  // доступен); остальным отдел жёстко задан их персоной — они создают сервер
  // только в своём отделе, dept-изоляцию всё равно энфорсит backend.
  const [departmentId, setDepartmentId] = useState(
    isAccountAdmin ? (depts[0]?.id ?? "") : (fixedDeptId ?? ""),
  );
  const [ipAddress, setIpAddress] = useState("");
  const [sshPort, setSshPort] = useState<string>("22");
  const [submitting, setSubmitting] = useState(false);
  const fixedDeptLabel = useDeptLabel(fixedDeptId);

  // backend кладёт ip_address в INET (IPv4Address | IPv6Address) и отбивает
  // мусор 422 ещё на pydantic; гасим заведомо-битый ввод заранее.
  const ipError =
    ipAddress.trim() && !isLikelyIpAddress(ipAddress.trim())
      ? "Ожидается IPv4 или IPv6 адрес"
      : null;

  // depts может прийти позже — подхватим первый, если ещё не выбран (только у
  // account_admin с выпадающим списком).
  if (isAccountAdmin && !departmentId && depts.length) {
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
      <div className="p-5 w-full">
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
            <span className="text-dim text-xs">Отображаемое имя</span>
            <input
              className="surface-2 border border-token rounded px-2 py-1"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="опционально"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">Отдел *</span>
            {isAccountAdmin ? (
              <select
                className="surface-2 border border-token rounded px-2 py-1"
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                required
              >
                {depts.length === 0 && (
                  <option value="">— нет отделов —</option>
                )}
                {depts.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <input
                  className="surface-2 border border-token rounded px-2 py-1 text-dim"
                  value={fixedDeptId ? fixedDeptLabel : "— нет отдела —"}
                  readOnly
                  title={fixedDeptId ?? ""}
                />
                <span className="text-[11px] text-dim">
                  сервер создаётся в вашем отделе
                </span>
              </>
            )}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">IP-адрес *</span>
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
            <span className="text-dim text-xs">SSH-порт</span>
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
