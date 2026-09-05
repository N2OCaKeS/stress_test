/**
 * Страница /server/packages — массовый запрос установленных пакетов по набору
 * серверов и ВМ отдела.
 *
 * Поток: оператор выбирает серверы и/или ВМ (мультиселект) и pattern
 * (shell-glob), жмёт «Запросить». Серверы уходят одним bulk-вызовом
 * (`POST /servers/installed-packages/bulk`); ВМ bulk-эндпоинта не имеют, поэтому
 * по каждой ставится отдельный probe `vm.list_packages` через
 * `GET /vms/{id}/packages?refresh=true`. Оба потока сходятся в одну сводную
 * таблицу. backend (`POST /servers/installed-packages/bulk`) сразу отдаёт
 * per-server исходы. Подготовленным серверам ставится probe-задача
 * (`status: ok`, `task_id`), остальным — статус-причина (`prepare_required` и
 * т.п.). Список пакетов приходит асинхронно в `task.result`, поэтому по каждому
 * `task_id` мы поллим `GET /tasks/{id}` до терминала и дотягиваем пакеты.
 *
 * Результат — сводная таблица с переключателем ориентации:
 *   режим A: строки = пакеты, столбцы = серверы (заголовок = hostname + версия
 *            ОС, ячейка = версия пакета на сервере);
 *   режим B: транспонированный (строки = серверы, столбцы = пакеты).
 * Статусы серверов (prepare_required и т.п.) видны в шапке/легенде — страница
 * не падает на них, просто показывает сервер без пакетов с пометкой статуса.
 * Текущую таблицу можно выгрузить в JSON и CSV.
 *
 * account_admin / logging_* отрезаны от server-зоны backend'ом — для них
 * BlockedPane. probe требует server.operator+ / dep_admin своего отдела;
 * backend перепроверит, клиентский гейт прячет заведомо лишнюю кнопку.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Package,
  Search,
  AlertCircle,
  RefreshCw,
  Download,
  Rows3,
  Columns3,
  Trash2,
  ArrowUpCircle,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { listServers } from "@/api/server/servers";
import {
  installedPackagesBulk,
  packagesBulkAction,
  getTask,
} from "@/api/server/misc";
import { listVms, listVmPackages, vmPackagesAction } from "@/api/server/vms";
import { listOsVersions } from "@/api/server/osVersions";
import { isDepAdmin, isServerZoneBlocked } from "@/lib/rbac";
import { isTerminalTaskStatus } from "@/api/server/types";
import type {
  OffsetPaginatedResponse,
  OsVersion,
  PackageInfo,
  PackagesBulkActionKind,
  PackagesBulkActionServerStatus,
  Server,
  TaskRead,
} from "@/api/server/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";

const SERVER_LIMIT = 200;
const POLL_MS = 3_000;
// Probe идёт по SSH (dpkg/rpm на 2-3 тыс. строк) — может длиться десятки секунд.
// Рвём поллинг с понятным статусом, чтобы спиннер не висел навсегда.
const POLL_TIMEOUT_MS = 3 * 60_000;

type Orientation = "packages-rows" | "servers-rows";

/** Человеко-читаемые подписи статусов сервера в bulk-ответе. */
const STATUS_LABEL: Record<string, string> = {
  ok: "ok",
  prepare_required: "не подготовлен",
  decommissioned: "decommissioned",
  not_found: "не найден",
  auth_failed: "auth failed",
  error: "недоступна",
};

const STATUS_KIND: Record<string, "ok" | "warn" | "danger" | ""> = {
  ok: "ok",
  prepare_required: "warn",
  decommissioned: "",
  not_found: "danger",
  auth_failed: "danger",
  error: "danger",
};

/** Разбивает строку фильтра на отдельные glob'ы по пробелам, без пустых. */
function parsePatterns(raw: string): string[] {
  return raw.split(/\s+/).filter(Boolean);
}

/** Достаёт `packages` из произвольного `task.result` (best-effort). */
function extractPackages(result: TaskRead["result"]): PackageInfo[] {
  if (!result || typeof result !== "object") return [];
  const raw = (result as Record<string, unknown>).packages;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((p): PackageInfo | null => {
      if (!p || typeof p !== "object") return null;
      const obj = p as Record<string, unknown>;
      if (typeof obj.name !== "string") return null;
      return {
        name: obj.name,
        version: typeof obj.version === "string" ? obj.version : "",
      };
    })
    .filter((p): p is PackageInfo => p !== null);
}

/** Тип сущности в сводке: физический сервер или ВМ. */
type EntityKind = "server" | "vm";

/** Нормализованный элемент левого мультиселекта — сервер или ВМ. */
interface PickEntity {
  kind: EntityKind;
  id: string;
  /** Отображаемое имя (display_name сервера / name ВМ). */
  name: string;
  hostname: string;
  ip: string | null;
  /** Готовая подпись ОС. */
  osName: string;
  /** OS-версия сервера (id каталога); для ВМ — null. */
  osVersionId: string | null;
  /** Подготовлена ли сущность (is_managed). */
  ready: boolean;
  /** Booking-статус ВМ (free / run test / …); для сервера — null. */
  statusText: string | null;
}

/**
 * Локальное состояние одной сущности (сервер или ВМ) в таблице: исход
 * dispatch'а + поллинг task'и. Сервер и ВМ ложатся в одну модель, чтобы
 * попадать в общую сводную таблицу наравне.
 */
interface ServerState {
  kind: EntityKind;
  serverId: string;
  hostname: string;
  /** Человекочитаемое имя сущности (если задано) — показываем его как основное. */
  displayName: string | null;
  /** OS-версия сервера (id из каталога). Для ВМ — null, имя лежит в osName. */
  osVersionId: string | null;
  /** Готовая подпись ОС для ВМ (backend отдаёт строкой). Для сервера — null. */
  osName: string | null;
  status: string;
  taskId: string | null;
  packages: PackageInfo[];
  /** true — по task'е ещё идёт поллинг result'а. */
  polling: boolean;
  /** Ошибка поллинга/задачи, если есть. */
  error: string | null;
}

function canProbe(
  persona: ReturnType<typeof usePersona>["persona"],
): boolean {
  if (persona.service_roles.server === "admin") return true;
  if (persona.service_roles.server === "operator") return true;
  if (isDepAdmin(persona)) return true;
  return false;
}

// Право manage_packages (install/remove/update). Backend выдаёт его той же
// тире, что и probe (server.operator+ / dep_admin своего отдела); финальную
// проверку делает сервер, гейт лишь прячет блок действий от reader/guest.
function canManagePackages(
  persona: ReturnType<typeof usePersona>["persona"],
): boolean {
  if (persona.service_roles.server === "admin") return true;
  if (persona.service_roles.server === "operator") return true;
  if (isDepAdmin(persona)) return true;
  return false;
}

/** Человеко-читаемые подписи статусов сущности в bulk-action ответе. */
const ACTION_STATUS_LABEL: Record<string, string> = {
  ok: "поставлено",
  prepare_required: "не подготовлен",
  reserved: "забронирован",
  decommissioned: "decommissioned",
  not_found: "не найден",
  // Синтетический статус для ВМ, по которой backend отбил сам dispatch
  // (права/prepare/hub) — деталь причины лежит в error.
  error: "отклонено",
};

const ACTION_STATUS_KIND: Record<string, "ok" | "warn" | "danger" | ""> = {
  ok: "ok",
  prepare_required: "warn",
  reserved: "warn",
  decommissioned: "",
  not_found: "danger",
  error: "danger",
};

const ACTION_LABEL: Record<PackagesBulkActionKind, string> = {
  install: "Установка",
  remove: "Удаление",
  update: "Обновление",
};

/** Локальное состояние одной сущности (сервер/ВМ) в результате action (исход + поллинг). */
interface ActionServerState {
  kind: EntityKind;
  serverId: string;
  hostname: string;
  /** Человекочитаемое имя сервера (если задано) — показываем его как основное. */
  displayName: string | null;
  status: PackagesBulkActionServerStatus;
  taskId: string | null;
  /** Терминальный статус задачи, когда она досчитана (succeeded/failed/cancelled). */
  taskStatus: string | null;
  polling: boolean;
  error: string | null;
}

export function ServerPackages() {
  const { persona } = usePersona();
  const toast = useToast();
  const zoneBlocked = isServerZoneBlocked(persona);

  // Каталог OS-версий тянем один раз, чтобы показывать имена вместо osv_*-id
  // в шапке таблицы и легенде. Каталог глобальный и небольшой.
  const osQ = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit: 200 }),
    [],
    { enabled: !zoneBlocked },
  );
  const osMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const v of osQ.data?.items ?? []) m.set(v.id, v.name);
    return m;
  }, [osQ.data]);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pattern, setPattern] = useState("");
  const [search, setSearch] = useState("");
  const [orientation, setOrientation] = useState<Orientation>("packages-rows");
  const [dispatching, setDispatching] = useState(false);
  const [dispatchErr, setDispatchErr] = useState<string | null>(null);
  // Серверные исходы после запуска bulk — ключ карты для рендера таблицы.
  const [states, setStates] = useState<ServerState[]>([]);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const serversQ = useQuery(
    () => listServers({ limit: SERVER_LIMIT }),
    [],
    { enabled: !zoneBlocked },
  );
  const servers = useMemo(() => serversQ.data?.items ?? [], [serversQ.data]);

  const vmsQ = useQuery(
    () => listVms({ limit: SERVER_LIMIT }),
    [],
    { enabled: !zoneBlocked },
  );
  const vms = useMemo(() => vmsQ.data?.items ?? [], [vmsQ.data]);

  const allowed = canProbe(persona);
  const canManage = canManagePackages(persona);

  const osLabel = useCallback(
    (id: string | null) => (id ? osMap.get(id) ?? id : "—"),
    [osMap],
  );

  // Подпись ОС для строки сводки: сервер резолвится через каталог, ВМ несёт
  // готовую строку в osName.
  const osOf = useCallback(
    (s: ServerState) =>
      s.kind === "vm" ? s.osName ?? "—" : osLabel(s.osVersionId),
    [osLabel],
  );

  // Общий пул выбора: серверы и ВМ в одной нормализованной форме, чтобы поиск,
  // «выбрать все» и запуск probe работали над ними единообразно.
  const serverEntities = useMemo<PickEntity[]>(
    () =>
      servers.map((s) => ({
        kind: "server",
        id: s.id,
        name: s.display_name ?? s.hostname,
        hostname: s.hostname,
        ip: s.ip_address,
        osName: osLabel(s.os_version_id),
        osVersionId: s.os_version_id,
        ready: s.is_managed,
        statusText: null,
      })),
    [servers, osLabel],
  );
  const vmEntities = useMemo<PickEntity[]>(
    () =>
      vms.map((v) => ({
        kind: "vm",
        id: v.id,
        name: v.name,
        hostname: v.hostname ?? v.name,
        ip: v.ip_address,
        osName: v.os_version ?? "—",
        osVersionId: null,
        ready: v.is_managed ?? false,
        statusText: v.status || null,
      })),
    [vms],
  );

  // Карта id → сущность: нужна на запуске, чтобы развести серверы и ВМ по своим
  // ветвям dispatch'а.
  const entityById = useMemo(() => {
    const m = new Map<string, PickEntity>();
    for (const e of serverEntities) m.set(e.id, e);
    for (const e of vmEntities) m.set(e.id, e);
    return m;
  }, [serverEntities, vmEntities]);

  const matchesSearch = useCallback(
    (e: PickEntity, term: string) =>
      e.name.toLowerCase().includes(term) ||
      e.hostname.toLowerCase().includes(term) ||
      (e.ip?.toLowerCase().includes(term) ?? false),
    [],
  );

  const filteredServerEntities = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return serverEntities;
    return serverEntities.filter((e) => matchesSearch(e, term));
  }, [serverEntities, search, matchesSearch]);
  const filteredVmEntities = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return vmEntities;
    return vmEntities.filter((e) => matchesSearch(e, term));
  }, [vmEntities, search, matchesSearch]);

  const filteredEntities = useMemo(
    () => [...filteredServerEntities, ...filteredVmEntities],
    [filteredServerEntities, filteredVmEntities],
  );
  const totalCount = serverEntities.length + vmEntities.length;

  // Разводим выбор на серверы и ВМ: панель действий шлёт их разными клиентами
  // (серверный bulk vs per-VM action), но исходы сводит в одну таблицу.
  const selectedServerIds = useMemo(
    () =>
      [...selected].filter((id) => entityById.get(id)?.kind === "server"),
    [selected, entityById],
  );
  const selectedVmEntities = useMemo(
    () =>
      [...selected]
        .map((id) => entityById.get(id))
        .filter((e): e is PickEntity => e?.kind === "vm"),
    [selected, entityById],
  );

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllVisible() {
    setSelected((prev) => {
      const next = new Set(prev);
      const allOn = filteredEntities.every((e) => next.has(e.id));
      for (const e of filteredEntities) {
        if (allOn) next.delete(e.id);
        else next.add(e.id);
      }
      return next;
    });
  }

  // Обновить состояние одного сервера по serverId (поллинг task'и).
  const patchState = useCallback(
    (serverId: string, patch: Partial<ServerState>) => {
      setStates((prev) =>
        prev.map((s) => (s.serverId === serverId ? { ...s, ...patch } : s)),
      );
    },
    [],
  );

  async function handleRun() {
    if (dispatching || !allowed) return;
    if (selected.size === 0) {
      setDispatchErr("Выберите хотя бы одну сущность (сервер или ВМ).");
      return;
    }
    setDispatchErr(null);
    setDispatching(true);
    setStates([]);
    try {
      const patterns = parsePatterns(pattern);
      const rawPattern = pattern.trim();
      const selectedIds = [...selected];
      const serverIds = selectedIds.filter(
        (id) => entityById.get(id)?.kind === "server",
      );
      const vmIds = selectedIds.filter(
        (id) => entityById.get(id)?.kind === "vm",
      );

      // Серверы — один bulk-вызов; ВМ — по probe на каждую (bulk-эндпоинта для
      // ВМ нет). Оба потока сходятся в одну таблицу states.
      const serverStatesP: Promise<ServerState[]> = serverIds.length
        ? installedPackagesBulk({
            server_ids: serverIds,
            ...(patterns.length ? { patterns } : {}),
          }).then((res) =>
            res.results.map((r) => ({
              kind: "server" as const,
              serverId: r.server_id,
              hostname: r.hostname ?? r.server_id,
              displayName:
                servers.find((s) => s.id === r.server_id)?.display_name ?? null,
              osVersionId: r.os_version_id,
              osName: null,
              status: r.status,
              taskId: r.task_id ?? null,
              packages: r.packages ?? [],
              // Поллим только серверы со статусом ok и непустым task_id, у
              // которых пакеты ещё не приехали в самом bulk-ответе.
              polling:
                r.status === "ok" &&
                !!r.task_id &&
                (r.packages?.length ?? 0) === 0,
              error: null,
            })),
          )
        : Promise.resolve([]);

      const vmStatesP: Promise<ServerState[]> = Promise.all(
        vmIds.map(async (vmId): Promise<ServerState> => {
          const ent = entityById.get(vmId);
          const base = {
            kind: "vm" as const,
            serverId: vmId,
            hostname: ent?.hostname ?? vmId,
            displayName: ent?.name ?? null,
            osVersionId: null,
            osName: ent?.osName ?? null,
            taskId: null as string | null,
          };
          try {
            const res = await listVmPackages(vmId, {
              refresh: true,
              ...(rawPattern ? { pattern: rawPattern } : {}),
            });
            return {
              ...base,
              status: "ok",
              taskId: res.task_id ?? null,
              // Показываем сохранённый срез, пока свежий probe считается.
              packages: res.packages.map((p) => ({
                name: p.name,
                version: p.version ?? "",
              })),
              polling: res.dispatched && !!res.task_id,
              error: null,
            };
          } catch (e) {
            return {
              ...base,
              status: "error",
              packages: [],
              polling: false,
              error: apiErrMsg(e, "ВМ недоступна для probe пакетов"),
            };
          }
        }),
      );

      const [serverStates, vmStates] = await Promise.all([
        serverStatesP,
        vmStatesP,
      ]);
      if (!aliveRef.current) return;
      setStates([...serverStates, ...vmStates]);
      const parts: string[] = [];
      if (serverIds.length) parts.push(`серверов: ${serverIds.length}`);
      if (vmIds.length) parts.push(`ВМ: ${vmIds.length}`);
      toast.success(`Запрос поставлен (${parts.join(", ")})`);
    } catch (e) {
      if (!aliveRef.current) return;
      const msg = apiErrMsg(e, "Не удалось запустить массовый запрос пакетов");
      setDispatchErr(msg);
      toast.error(msg);
    } finally {
      if (aliveRef.current) setDispatching(false);
    }
  }

  // Поллинг task-result'ов по серверам со статусом ok. Один интервал на всех —
  // на каждом тике дёргаем GET /tasks/{id} для ещё активных и гасим terminal'ы.
  const polledRef = useRef<Map<string, number>>(new Map());
  useEffect(() => {
    const active = states.filter((s) => s.polling && s.taskId);
    if (active.length === 0) {
      polledRef.current.clear();
      return;
    }
    // Засекаем старт поллинга на каждый task один раз — для таймаута.
    const started = polledRef.current;
    for (const s of active) {
      if (s.taskId && !started.has(s.taskId)) started.set(s.taskId, Date.now());
    }
    let stopped = false;
    const tick = () => {
      for (const s of active) {
        if (!s.taskId) continue;
        const startedAt = started.get(s.taskId) ?? Date.now();
        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          patchState(s.serverId, {
            polling: false,
            error: "Worker не закрыл задачу за отведённое время",
          });
          continue;
        }
        getTask(s.taskId)
          .then((t) => {
            if (stopped || !aliveRef.current) return;
            if (!isTerminalTaskStatus(t.status)) return;
            if (t.status === "failed") {
              patchState(s.serverId, {
                polling: false,
                error: t.last_error ?? "Задача завершилась ошибкой",
              });
            } else {
              patchState(s.serverId, {
                polling: false,
                packages: extractPackages(t.result),
              });
            }
          })
          .catch((e: unknown) => {
            if (stopped || !aliveRef.current) return;
            patchState(s.serverId, {
              polling: false,
              error: apiErrMsg(e, "Не удалось прочитать результат задачи"),
            });
          });
      }
    };
    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [states, patchState]);

  const anyPolling = states.some((s) => s.polling);

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_service / packages">
        <BlockedPane />
      </Shell>
    );
  }

  // Серверы — первичный список: их ошибка блокирует панель. Список ВМ
  // деградирует мягко (мелкая пометка), чтобы недоступность vm-домена не
  // рушила серверный сценарий.
  const listLoading = serversQ.loading || vmsQ.loading;

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${totalCount} серверам и ВМ…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-[11px] text-dim">
          <span>Выбрано: {selected.size}</span>
          <Button variant="ghost" size="sm"
            type="button"
            onClick={toggleAllVisible}
            disabled={filteredEntities.length === 0}
          >
            {filteredEntities.every((e) => selected.has(e.id)) &&
            filteredEntities.length > 0
              ? "Снять все"
              : "Выбрать все"}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {listLoading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {!listLoading && serversQ.error && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(serversQ.error, "Список серверов не загрузился")}</div>
              <Button variant="ghost"
                className="mt-2"
                onClick={() => serversQ.refetch()}
              >
                Повторить
              </Button>
            </div>
          </div>
        )}
        {!listLoading && !serversQ.error && filteredEntities.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            {totalCount > 0 ? "Под фильтр ничего нет." : "Список пуст."}
          </div>
        )}
        {!listLoading && !serversQ.error && filteredServerEntities.length > 0 && (
          <>
            <div className="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-wide text-dim">
              Серверы ({filteredServerEntities.length})
            </div>
            <div className="px-2 flex flex-col gap-0.5">
              {filteredServerEntities.map((e) => (
                <EntityPickRow
                  key={e.id}
                  entity={e}
                  checked={selected.has(e.id)}
                  onToggle={() => toggle(e.id)}
                />
              ))}
            </div>
          </>
        )}
        {!listLoading && filteredVmEntities.length > 0 && (
          <>
            <div className="px-3 pt-3 pb-0.5 text-[10px] uppercase tracking-wide text-dim">
              ВМ ({filteredVmEntities.length})
            </div>
            <div className="px-2 flex flex-col gap-0.5">
              {filteredVmEntities.map((e) => (
                <EntityPickRow
                  key={e.id}
                  entity={e}
                  checked={selected.has(e.id)}
                  onToggle={() => toggle(e.id)}
                />
              ))}
            </div>
          </>
        )}
        {!listLoading && vmsQ.error && (
          <div className="px-3 pt-3 text-[11px] text-dim italic">
            Список ВМ не загрузился — доступны только серверы.
          </div>
        )}
      </div>

      <div className="border-t border-token p-3 shrink-0 flex flex-col gap-2">
        <label className="flex flex-col gap-1 text-xs text-dim">
          паттерны (shell glob, через пробел)
          <input
            className="input mono text-xs"
            placeholder="ssh* bash* *libs*"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
          />
          <span className="text-[10px] text-dim">
            Несколько шаблонов через пробел; пусто — все пакеты (
            <span className="mono">*</span>).
          </span>
        </label>
        {allowed ? (
          <Button variant="primary"
            className="w-full flex items-center justify-center gap-2"
            onClick={handleRun}
            disabled={dispatching || selected.size === 0}
          >
            <RefreshCw
              className={`w-4 h-4 ${dispatching ? "animate-spin" : ""}`}
            />
            {dispatching ? "Запускаем…" : `Запросить (${selected.size})`}
          </Button>
        ) : (
          <div className="text-[11px] text-dim italic">
            Нет прав на запуск probe (нужна роль server.operator+ или dep_admin
            своего департамента).
          </div>
        )}

        {canManage && (
          <PackageActionPanel
            serverIds={selectedServerIds}
            servers={servers}
            vms={selectedVmEntities}
          />
        )}
      </div>
    </aside>
  );

  return (
    <Shell breadcrumb="server_service / packages" middle={aside}>
      <PackagesWorkzone
        states={states}
        orientation={orientation}
        onOrientation={setOrientation}
        anyPolling={anyPolling}
        dispatchErr={dispatchErr}
        pattern={parsePatterns(pattern).join(" ") || "*"}
        osOf={osOf}
      />
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function EntityPickRow({
  entity,
  checked,
  onToggle,
}: {
  entity: PickEntity;
  checked: boolean;
  onToggle: () => void;
}) {
  const notReadyTitle =
    entity.kind === "vm" ? "ВМ не подготовлена" : "Сервер не подготовлен";
  return (
    <label
      className={`cred-row text-left flex items-center gap-2 cursor-pointer ${checked ? "active" : ""}`}
    >
      <Checkbox checked={checked} onChange={onToggle} />
      <div className="flex-1 min-w-0">
        <div
          className="text-sm truncate flex items-center gap-1.5"
          title={entity.ip ?? undefined}
        >
          {entity.kind === "vm" && (
            <Badge kind="accent" className="text-[9px]">ВМ</Badge>
          )}
          <span className="truncate">{entity.name}</span>
        </div>
        <div className="text-[11px] text-dim truncate">
          ОС: {entity.osName}
        </div>
      </div>
      {!entity.ready && (
        <Badge kind="warn" title={notReadyTitle}>
          не готов
        </Badge>
      )}
    </label>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Блок действий: install / remove / update по выбранным серверам
// ───────────────────────────────────────────────────────────────────────────

/**
 * Панель массовых действий над пакетами выбранных серверов и ВМ. Видна только
 * носителю права `manage_packages`. Принимает список пакетов (через пробел),
 * кнопки Install / Remove / Update; Remove и Update идут с подтверждением
 * (деструктив). Серверы уходят одним bulk-вызовом, ВМ — по отдельному
 * `vmPackagesAction` на каждую (bulk-эндпоинта для ВМ нет). Исходы обоих потоков
 * сходятся в одну таблицу; итог каждой задачи поллится до терминала.
 */
function PackageActionPanel({
  serverIds,
  servers,
  vms,
}: {
  serverIds: string[];
  servers: Server[];
  vms: PickEntity[];
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const [pkgInput, setPkgInput] = useState("");
  const [running, setRunning] = useState<PackagesBulkActionKind | null>(null);
  const [actionLabel, setActionLabel] = useState<string | null>(null);
  const [states, setStates] = useState<ActionServerState[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const hostnameOf = useCallback(
    (id: string) => servers.find((s) => s.id === id)?.hostname ?? id,
    [servers],
  );

  const packages = useMemo(() => parsePatterns(pkgInput), [pkgInput]);

  const patchState = useCallback(
    (serverId: string, patch: Partial<ActionServerState>) => {
      setStates((prev) =>
        prev.map((s) => (s.serverId === serverId ? { ...s, ...patch } : s)),
      );
    },
    [],
  );

  const targetCount = serverIds.length + vms.length;

  async function dispatch(action: PackagesBulkActionKind) {
    if (running) return;
    if (targetCount === 0) {
      setErr("Выберите хотя бы один сервер или ВМ.");
      return;
    }
    // install/remove требуют явного списка пакетов; update без списка =
    // upgrade всех пакетов на боксе.
    if ((action === "install" || action === "remove") && packages.length === 0) {
      setErr("Укажите хотя бы один пакет для этого действия.");
      return;
    }

    if (action === "remove" || action === "update") {
      const pkgText =
        packages.length > 0 ? packages.join(", ") : "все пакеты (upgrade)";
      const ok = await confirm({
        title: action === "remove" ? "Удалить пакеты" : "Обновить пакеты",
        message:
          action === "remove"
            ? `Удалить ${pkgText} на ${targetCount} сущностях (серверы и ВМ)? Операция необратима.`
            : `Обновить ${pkgText} на ${targetCount} сущностях (серверы и ВМ)?`,
        confirmLabel: action === "remove" ? "Удалить" : "Обновить",
        danger: action === "remove",
      });
      if (!ok) return;
    }

    setErr(null);
    setStates([]);
    setRunning(action);
    setActionLabel(ACTION_LABEL[action]);
    try {
      // Серверы — один bulk-вызов; ВМ — по action на каждую (bulk для ВМ нет).
      // Отбой прав/prepare по конкретной ВМ ловим локально, чтобы не ронять
      // серверный поток и остальные ВМ.
      const serverStatesP: Promise<ActionServerState[]> = serverIds.length
        ? packagesBulkAction({
            server_ids: serverIds,
            action,
            ...(packages.length ? { packages } : {}),
          }).then((res) =>
            res.results.map((r) => ({
              kind: "server" as const,
              serverId: r.server_id,
              hostname: r.hostname ?? hostnameOf(r.server_id),
              displayName:
                servers.find((s) => s.id === r.server_id)?.display_name ?? null,
              status: r.status,
              taskId: r.task_id ?? null,
              taskStatus: null,
              polling: r.status === "ok" && !!r.task_id,
              error: null,
            })),
          )
        : Promise.resolve([]);

      const vmStatesP: Promise<ActionServerState[]> = Promise.all(
        vms.map(async (vm): Promise<ActionServerState> => {
          const base = {
            kind: "vm" as const,
            serverId: vm.id,
            hostname: vm.hostname,
            displayName: vm.name,
            taskStatus: null as string | null,
          };
          try {
            const res = await vmPackagesAction(vm.id, {
              action,
              ...(packages.length ? { packages } : {}),
            });
            return {
              ...base,
              status: "ok",
              taskId: res.task_id ?? null,
              polling: !!res.task_id,
              error: null,
            };
          } catch (e) {
            return {
              ...base,
              status: "error",
              taskId: null,
              polling: false,
              error: apiErrMsg(e, "ВМ отклонила действие с пакетами"),
            };
          }
        }),
      );

      const [serverStates, vmStates] = await Promise.all([
        serverStatesP,
        vmStatesP,
      ]);
      if (!aliveRef.current) return;
      setStates([...serverStates, ...vmStates]);
      const dispatched = [...serverStates, ...vmStates].filter(
        (s) => s.status === "ok" && s.taskId,
      ).length;
      toast.success(
        `${ACTION_LABEL[action]}: задач поставлено ${dispatched} из ${targetCount}`,
      );
    } catch (e) {
      if (!aliveRef.current) return;
      const msg = apiErrMsg(e, "Не удалось поставить массовое действие");
      setErr(msg);
      toast.error(msg);
    } finally {
      if (aliveRef.current) setRunning(null);
    }
  }

  // Поллинг исходов задач по серверам со статусом ok. Один интервал на всех:
  // на каждом тике дёргаем GET /tasks/{id} для активных и гасим terminal'ы.
  const startedRef = useRef<Map<string, number>>(new Map());
  useEffect(() => {
    const active = states.filter((s) => s.polling && s.taskId);
    if (active.length === 0) {
      startedRef.current.clear();
      return;
    }
    const started = startedRef.current;
    for (const s of active) {
      if (s.taskId && !started.has(s.taskId)) started.set(s.taskId, Date.now());
    }
    let stopped = false;
    const tick = () => {
      for (const s of active) {
        if (!s.taskId) continue;
        const startedAt = started.get(s.taskId) ?? Date.now();
        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          patchState(s.serverId, {
            polling: false,
            error: "Worker не закрыл задачу за отведённое время",
          });
          continue;
        }
        getTask(s.taskId)
          .then((t) => {
            if (stopped || !aliveRef.current) return;
            if (!isTerminalTaskStatus(t.status)) return;
            if (t.status === "failed") {
              patchState(s.serverId, {
                polling: false,
                taskStatus: t.status,
                error: t.last_error ?? "Задача завершилась ошибкой",
              });
            } else {
              patchState(s.serverId, {
                polling: false,
                taskStatus: t.status,
              });
            }
          })
          .catch((e: unknown) => {
            if (stopped || !aliveRef.current) return;
            patchState(s.serverId, {
              polling: false,
              error: apiErrMsg(e, "Не удалось прочитать результат задачи"),
            });
          });
      }
    };
    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [states, patchState]);

  const busy = running !== null;

  return (
    <div className="mt-3 border-t border-token pt-3 flex flex-col gap-2">
      <div className="text-xs text-dim font-medium">
        Действия с пакетами ({serverIds.length} серв.
        {vms.length > 0 && ` + ${vms.length} ВМ`})
      </div>
      <label className="flex flex-col gap-1 text-xs text-dim">
        пакеты (через пробел)
        <input
          className="input mono text-xs"
          placeholder="htop nginx git"
          value={pkgInput}
          onChange={(e) => setPkgInput(e.target.value)}
          disabled={busy}
        />
        <span className="text-[10px] text-dim">
          Для установки/удаления список обязателен; для обновления пусто =
          upgrade всех пакетов.
        </span>
      </label>
      <div className="grid grid-cols-3 gap-1.5">
        <Button size="sm"
          type="button"
          className="flex items-center justify-center gap-1"
          onClick={() => dispatch("install")}
          disabled={busy || targetCount === 0}
          title="Установить пакеты"
        >
          <Download className="w-3.5 h-3.5" /> Установить
        </Button>
        <Button variant="danger" size="sm"
          type="button"
          className="flex items-center justify-center gap-1"
          onClick={() => dispatch("remove")}
          disabled={busy || targetCount === 0}
          title="Удалить пакеты"
        >
          <Trash2 className="w-3.5 h-3.5" /> Удалить
        </Button>
        <Button size="sm"
          type="button"
          className="flex items-center justify-center gap-1"
          onClick={() => dispatch("update")}
          disabled={busy || targetCount === 0}
          title="Обновить пакеты (пусто = upgrade всех)"
        >
          <ArrowUpCircle className="w-3.5 h-3.5" /> Обновить
        </Button>
      </div>

      {err && (
        <div className="alert alert-danger flex items-start gap-2 text-[11px]">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span className="flex-1">{err}</span>
        </div>
      )}

      {states.length > 0 && (
        <div className="flex flex-col gap-1">
          {actionLabel && (
            <div className="text-[11px] text-dim">
              {actionLabel} — исходы по серверам и ВМ:
            </div>
          )}
          <div className="flex flex-col gap-0.5 max-h-48 overflow-y-auto">
            {states.map((s) => (
              <ActionStatusRow key={s.serverId} state={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Одна строка исхода действия по серверу: статус + результат поллинга. */
function ActionStatusRow({ state }: { state: ActionServerState }) {
  const kind = ACTION_STATUS_KIND[state.status] ?? "";
  return (
    <div className="surface-2 border border-token rounded px-2 py-1 text-[11px] flex items-center gap-2">
      <span
        className="truncate flex-1 flex items-center gap-1"
        title={`${state.hostname} · ${state.serverId}`}
      >
        {state.kind === "vm" && (
          <Badge kind="accent" className="text-[9px]">ВМ</Badge>
        )}
        <span className="truncate">{state.displayName ?? state.hostname}</span>
      </span>
      <Badge kind={kind || "neutral"}>
        {ACTION_STATUS_LABEL[state.status] ?? state.status}
      </Badge>
      {state.polling && (
        <RefreshCw className="w-3 h-3 animate-spin text-dim" />
      )}
      {!state.polling && state.error && (
        <span className="text-danger" title={state.error}>
          ошибка
        </span>
      )}
      {!state.polling && !state.error && state.taskStatus === "succeeded" && (
        <span className="text-ok" title="Задача завершена">
          готово
        </span>
      )}
      {!state.polling && !state.error && state.taskStatus === "cancelled" && (
        <span className="text-dim">отменено</span>
      )}
    </div>
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
          серверов. Работайте под департаментной ролью (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Сводная таблица + переключатель ориентации + экспорт
// ───────────────────────────────────────────────────────────────────────────

/** Сохранить текст в файл через временный object-URL. */
function downloadText(filename: string, mime: string, text: string) {
  if (typeof document === "undefined") return;
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Экранирование значения для CSV (запятые, кавычки, переводы строк). */
function csvCell(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function PackagesWorkzone({
  states,
  orientation,
  onOrientation,
  anyPolling,
  dispatchErr,
  pattern,
  osOf,
}: {
  states: ServerState[];
  orientation: Orientation;
  onOrientation: (o: Orientation) => void;
  anyPolling: boolean;
  dispatchErr: string | null;
  pattern: string;
  osOf: (s: ServerState) => string;
}) {
  // Полный отсортированный список имён пакетов по всем серверам.
  const packageNames = useMemo(() => {
    const set = new Set<string>();
    for (const s of states) for (const p of s.packages) set.add(p.name);
    return [...set].sort((a, b) => a.localeCompare(b));
  }, [states]);

  // Быстрый доступ к версии пакета на сервере: server -> (name -> version).
  const versionMap = useMemo(() => {
    const map = new Map<string, Map<string, string>>();
    for (const s of states) {
      const inner = new Map<string, string>();
      for (const p of s.packages) inner.set(p.name, p.version);
      map.set(s.serverId, inner);
    }
    return map;
  }, [states]);

  const cell = (serverId: string, pkg: string) =>
    versionMap.get(serverId)?.get(pkg) ?? "";

  function exportJson() {
    const payload = {
      pattern,
      exported_at: new Date().toISOString(),
      servers: states.map((s) => ({
        kind: s.kind,
        server_id: s.serverId,
        hostname: s.hostname,
        display_name: s.displayName,
        os_version_id: s.osVersionId,
        os_version: s.osName,
        status: s.status,
        packages: s.packages,
      })),
    };
    downloadText(
      "packages.json",
      "application/json",
      JSON.stringify(payload, null, 2),
    );
  }

  function exportCsv() {
    // Матрица пакеты×серверы (режим A) как канонический CSV: первая колонка —
    // имя пакета, дальше по колонке на сервер с версией в ячейке.
    const header = [
      "package",
      ...states.map(
        (s) => `${s.displayName ?? s.hostname} (${osOf(s)})`,
      ),
    ];
    const lines = [header.map(csvCell).join(",")];
    for (const name of packageNames) {
      const row = [name, ...states.map((s) => cell(s.serverId, name))];
      lines.push(row.map(csvCell).join(","));
    }
    downloadText("packages.csv", "text/csv", lines.join("\r\n"));
  }

  const hasData = states.length > 0;
  const hasPackages = packageNames.length > 0;

  return (
    <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3 flex-wrap">
        <Package className="w-5 h-5 text-accent shrink-0" />
        <div className="flex-1 min-w-0">
          <h1 className="text-base font-semibold">Установленные пакеты — массово</h1>
          <div className="text-[11px] text-dim">
            pattern: <span className="mono">{pattern}</span>
            {hasData && <> · сущностей: {states.length}</>}
            {hasPackages && <> · уникальных пакетов: {packageNames.length}</>}
          </div>
        </div>
        {hasData && (
          <>
            <div className="flex items-center gap-1 surface-2 border border-token rounded p-0.5">
              <Button
                type="button"
                size="sm"
                variant={orientation === "packages-rows" ? "primary" : "ghost"}
                className="flex items-center gap-1"
                onClick={() => onOrientation("packages-rows")}
                title="Строки = пакеты, столбцы = серверы"
              >
                <Rows3 className="w-3.5 h-3.5" /> пакеты × серверы
              </Button>
              <Button
                type="button"
                size="sm"
                variant={orientation === "servers-rows" ? "primary" : "ghost"}
                className="flex items-center gap-1"
                onClick={() => onOrientation("servers-rows")}
                title="Строки = серверы, столбцы = пакеты"
              >
                <Columns3 className="w-3.5 h-3.5" /> серверы × пакеты
              </Button>
            </div>
            <Button size="sm"
              type="button"
              className="flex items-center gap-1"
              onClick={exportJson}
              title="Экспорт текущей таблицы в JSON"
            >
              <Download className="w-3.5 h-3.5" /> JSON
            </Button>
            <Button size="sm"
              type="button"
              className="flex items-center gap-1"
              onClick={exportCsv}
              title="Экспорт текущей таблицы в CSV"
            >
              <Download className="w-3.5 h-3.5" /> CSV
            </Button>
          </>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-5 flex flex-col gap-4">
        {dispatchErr && (
          <div className="alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="text-xs flex-1">{dispatchErr}</div>
          </div>
        )}

        {!hasData ? (
          <div className="empty-card max-w-md mx-auto text-center mt-10">
            <Package className="w-10 h-10 mx-auto text-dim mb-3" />
            <div className="text-sm text-dim">
              Выберите серверы или ВМ слева, задайте pattern и нажмите
              «Запросить».
            </div>
          </div>
        ) : (
          <>
            {anyPolling && (
              <div className="surface-2 border border-token rounded p-3 text-xs flex items-center gap-2">
                <RefreshCw className="w-4 h-4 animate-spin text-accent" />
                <span>
                  Ждём worker по части серверов — таблица дополнится по мере
                  готовности задач.
                </span>
              </div>
            )}

            <StatusLegend states={states} osOf={osOf} />

            {!hasPackages ? (
              <div className="text-xs text-dim italic">
                Пакетов пока нет. Серверы со статусом «ok» дополнят таблицу, как
                только worker закроет задачи; остальные показаны в легенде со
                своим статусом.
              </div>
            ) : (
              <>
                <MatrixMarkerHint />
                {orientation === "packages-rows" ? (
                  <PackagesByRows
                    states={states}
                    packageNames={packageNames}
                    cell={cell}
                    osOf={osOf}
                  />
                ) : (
                  <ServersByRows
                    states={states}
                    packageNames={packageNames}
                    cell={cell}
                    osOf={osOf}
                  />
                )}
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/** Шапка-легенда: статус каждой сущности (сервера/ВМ) в наборе. */
function StatusLegend({
  states,
  osOf,
}: {
  states: ServerState[];
  osOf: (s: ServerState) => string;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {states.map((s) => {
        const kind = STATUS_KIND[s.status] ?? "";
        return (
          <div
            key={s.serverId}
            className="surface-2 border border-token rounded px-2 py-1 text-[11px] flex items-center gap-2"
            title={`${s.hostname} · ОС: ${osOf(s)}`}
          >
            <span className="flex flex-col leading-tight min-w-0">
              <span className="truncate max-w-[160px] flex items-center gap-1">
                {s.kind === "vm" && (
                  <Badge kind="accent" className="text-[9px]">ВМ</Badge>
                )}
                {s.displayName ?? s.hostname}
              </span>
              {s.displayName && (
                <span className="mono text-[10px] text-dim truncate max-w-[160px]">
                  {s.hostname}
                </span>
              )}
            </span>
              <Badge kind={kind || "neutral"}>
                {STATUS_LABEL[s.status] ?? s.status}
              </Badge>
            {s.polling && (
              <RefreshCw className="w-3 h-3 animate-spin text-dim" />
            )}
            {s.error && (
              <span className="text-danger" title={s.error}>
                ошибка
              </span>
            )}
            {!s.polling && !s.error && s.status === "ok" && (
              <span className="text-dim">{s.packages.length} пак.</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Сервер опрошен до конца: probe-задача поставлена (`ok`), поллинг завершён и
 * ошибки нет — значит его список пакетов окончательный. Пустая ячейка такого
 * сервера = пакет точно не установлен. Для остальных серверов (не подготовлен,
 * ещё поллится, ошибка и т.п.) пустота означает «неизвестно», а не «нет пакета».
 */
function isProbed(s: ServerState): boolean {
  return s.status === "ok" && !s.polling && !s.error;
}

/**
 * Ячейка версии пакета на сервере. Версия — как есть; для опрошенного сервера
 * без пакета — приглушённый «—» (точно не установлен); для неопрошенного —
 * ещё более бледный «·» с подсказкой статуса, чтобы не путать с отсутствием.
 */
function PackageCell({ server, version }: { server: ServerState; version: string }) {
  const base = "px-3 py-1.5 mono text-xs whitespace-nowrap";
  if (version) {
    return <td className={base}>{version}</td>;
  }
  if (isProbed(server)) {
    return (
      <td className={`${base} text-dim`} title="Пакет не установлен">
        —
      </td>
    );
  }
  const label = STATUS_LABEL[server.status] ?? server.status;
  return (
    <td
      className={`${base} text-dim opacity-50`}
      title={`Сервер не опрошен (${label})`}
    >
      ·
    </td>
  );
}

/** Подпись к матрице: что означают маркеры пустых ячеек. */
function MatrixMarkerHint() {
  return (
    <div className="text-[11px] text-dim flex flex-wrap items-center gap-x-3 gap-y-1">
      <span>
        <span className="mono">—</span> пакет не установлен
      </span>
      <span>
        <span className="mono opacity-50">·</span> сервер не опрошен (см. статусы
        выше)
      </span>
    </div>
  );
}

/** Режим A: строки = пакеты, столбцы = сущности (серверы/ВМ). */
function PackagesByRows({
  states,
  packageNames,
  cell,
  osOf,
}: {
  states: ServerState[];
  packageNames: string[];
  cell: (serverId: string, pkg: string) => string;
  osOf: (s: ServerState) => string;
}) {
  return (
    <div className="surface-2 border border-token rounded overflow-auto">
      <table className="text-sm border-collapse">
        <thead>
          <tr className="text-[11px] uppercase text-dim border-b border-token">
            <th className="text-left px-3 py-2 font-medium sticky left-0 surface-2 z-10">
              Пакет
            </th>
            {states.map((s) => (
              <th
                key={s.serverId}
                className="text-left px-3 py-2 font-medium whitespace-nowrap"
                title={s.serverId}
              >
                <div className="flex items-center gap-1">
                  {s.kind === "vm" && (
                    <Badge kind="accent" className="text-[9px]">ВМ</Badge>
                  )}
                  {s.displayName ?? s.hostname}
                </div>
                {s.displayName && (
                  <div className="mono text-dim font-normal normal-case">
                    {s.hostname}
                  </div>
                )}
                <div className="text-dim normal-case font-normal">
                  ОС: {osOf(s)}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {packageNames.map((name) => (
            <tr key={name} className="border-b border-token last:border-b-0">
              <td className="px-3 py-1.5 mono text-xs sticky left-0 surface-2">
                {name}
              </td>
              {states.map((s) => (
                <PackageCell
                  key={s.serverId}
                  server={s}
                  version={cell(s.serverId, name)}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Режим B: строки = сущности (серверы/ВМ), столбцы = пакеты (транспонированный). */
function ServersByRows({
  states,
  packageNames,
  cell,
  osOf,
}: {
  states: ServerState[];
  packageNames: string[];
  cell: (serverId: string, pkg: string) => string;
  osOf: (s: ServerState) => string;
}) {
  return (
    <div className="surface-2 border border-token rounded overflow-auto">
      <table className="text-sm border-collapse">
        <thead>
          <tr className="text-[11px] uppercase text-dim border-b border-token">
            <th className="text-left px-3 py-2 font-medium sticky left-0 surface-2 z-10">
              Сервер / ВМ
            </th>
            {packageNames.map((name) => (
              <th
                key={name}
                className="text-left px-3 py-2 font-medium mono whitespace-nowrap normal-case"
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {states.map((s) => (
            <tr key={s.serverId} className="border-b border-token last:border-b-0">
              <td className="px-3 py-1.5 sticky left-0 surface-2 whitespace-nowrap">
                <div className="text-xs font-medium flex items-center gap-1">
                  {s.kind === "vm" && (
                    <Badge kind="accent" className="text-[9px]">ВМ</Badge>
                  )}
                  {s.displayName ?? s.hostname}
                </div>
                {s.displayName && (
                  <div className="mono text-[10px] text-dim">{s.hostname}</div>
                )}
                <div className="text-[10px] text-dim">
                  ОС: {osOf(s)}
                </div>
              </td>
              {packageNames.map((name) => (
                <PackageCell
                  key={name}
                  server={s}
                  version={cell(s.serverId, name)}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
