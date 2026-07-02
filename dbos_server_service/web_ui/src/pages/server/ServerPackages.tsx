/**
 * Страница /server/packages — массовый запрос установленных пакетов по набору
 * серверов отдела.
 *
 * Поток: оператор выбирает серверы (мультиселект) и pattern (shell-glob), жмёт
 * «Запросить» — backend (`POST /servers/installed-packages/bulk`) сразу отдаёт
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
import { listOsVersions } from "@/api/server/osVersions";
import { isDepAdmin, isServerZoneBlocked } from "@/lib/rbac";
import { isTerminalTaskStatus } from "@/api/server/types";
import type {
  BulkPackagesServerStatus,
  OffsetPaginatedResponse,
  OsVersion,
  PackageInfo,
  PackagesBulkActionKind,
  PackagesBulkActionServerStatus,
  Server,
  TaskRead,
} from "@/api/server/types";

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
};

const STATUS_KIND: Record<string, "ok" | "warn" | "danger" | ""> = {
  ok: "ok",
  prepare_required: "warn",
  decommissioned: "",
  not_found: "danger",
  auth_failed: "danger",
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

/** Локальное состояние одного сервера в таблице (исход + поллинг task'и). */
interface ServerState {
  serverId: string;
  hostname: string;
  /** Человекочитаемое имя сервера (если задано) — показываем его как основное. */
  displayName: string | null;
  osVersionId: string | null;
  status: BulkPackagesServerStatus;
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

/** Человеко-читаемые подписи статусов сервера в bulk-action ответе. */
const ACTION_STATUS_LABEL: Record<string, string> = {
  ok: "поставлено",
  prepare_required: "не подготовлен",
  reserved: "забронирован",
  decommissioned: "decommissioned",
  not_found: "не найден",
};

const ACTION_STATUS_KIND: Record<string, "ok" | "warn" | "danger" | ""> = {
  ok: "ok",
  prepare_required: "warn",
  reserved: "warn",
  decommissioned: "",
  not_found: "danger",
};

const ACTION_LABEL: Record<PackagesBulkActionKind, string> = {
  install: "Установка",
  remove: "Удаление",
  update: "Обновление",
};

/** Локальное состояние одного сервера в результате bulk-action (исход + поллинг). */
interface ActionServerState {
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

  const allowed = canProbe(persona);
  const canManage = canManagePackages(persona);

  const filteredServers = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return servers;
    return servers.filter(
      (s) =>
        s.hostname.toLowerCase().includes(term) ||
        (s.display_name?.toLowerCase().includes(term) ?? false) ||
        s.ip_address.toLowerCase().includes(term),
    );
  }, [servers, search]);

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
      const allOn = filteredServers.every((s) => next.has(s.id));
      for (const s of filteredServers) {
        if (allOn) next.delete(s.id);
        else next.add(s.id);
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
      setDispatchErr("Выберите хотя бы один сервер.");
      return;
    }
    setDispatchErr(null);
    setDispatching(true);
    setStates([]);
    try {
      const patterns = parsePatterns(pattern);
      const res = await installedPackagesBulk({
        server_ids: [...selected],
        ...(patterns.length ? { patterns } : {}),
      });
      if (!aliveRef.current) return;
      const init: ServerState[] = res.results.map((r) => ({
        serverId: r.server_id,
        hostname: r.hostname ?? r.server_id,
        displayName:
          servers.find((s) => s.id === r.server_id)?.display_name ?? null,
        osVersionId: r.os_version_id,
        status: r.status,
        taskId: r.task_id ?? null,
        packages: r.packages ?? [],
        // Поллим только серверы со статусом ok и непустым task_id, у которых
        // пакеты ещё не приехали в самом bulk-ответе.
        polling:
          r.status === "ok" &&
          !!r.task_id &&
          (r.packages?.length ?? 0) === 0,
        error: null,
      }));
      setStates(init);
      toast.success(
        `Запрос поставлен: ${res.dispatched} из ${res.requested} серверов`,
      );
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

  const osLabel = (id: string | null) => (id ? osMap.get(id) ?? id : "—");

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${servers.length} серверам…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-[11px] text-dim">
          <span>Выбрано: {selected.size}</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={toggleAllVisible}
            disabled={filteredServers.length === 0}
          >
            {filteredServers.every((s) => selected.has(s.id)) &&
            filteredServers.length > 0
              ? "Снять все"
              : "Выбрать все"}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {serversQ.loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {serversQ.error && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(serversQ.error, "Список не загрузился")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => serversQ.refetch()}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
        {!serversQ.loading && !serversQ.error && filteredServers.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            {servers.length > 0 ? "Под фильтр серверов нет." : "Список пуст."}
          </div>
        )}
        <div className="px-2 flex flex-col gap-0.5">
          {filteredServers.map((s) => (
            <ServerPickRow
              key={s.id}
              server={s}
              checked={selected.has(s.id)}
              osLabel={osLabel}
              onToggle={() => toggle(s.id)}
            />
          ))}
        </div>
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
          <button
            className="btn btn-primary w-full flex items-center justify-center gap-2"
            onClick={handleRun}
            disabled={dispatching || selected.size === 0}
          >
            <RefreshCw
              className={`w-4 h-4 ${dispatching ? "animate-spin" : ""}`}
            />
            {dispatching ? "Запускаем…" : `Запросить (${selected.size})`}
          </button>
        ) : (
          <div className="text-[11px] text-dim italic">
            Нет прав на запуск probe (нужна роль server.operator+ или dep_admin
            своего департамента).
          </div>
        )}

        {canManage && (
          <PackageActionPanel
            serverIds={[...selected]}
            servers={servers}
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
        osLabel={osLabel}
      />
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function ServerPickRow({
  server,
  checked,
  osLabel,
  onToggle,
}: {
  server: Server;
  checked: boolean;
  osLabel: (id: string | null) => string;
  onToggle: () => void;
}) {
  const name = server.display_name ?? server.hostname;
  return (
    <label
      className={`cred-row text-left flex items-center gap-2 cursor-pointer ${checked ? "active" : ""}`}
    >
      <input type="checkbox" checked={checked} onChange={onToggle} />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate" title={server.ip_address}>
          {name}
        </div>
        <div className="text-[11px] text-dim truncate">
          ОС: {osLabel(server.os_version_id)}
        </div>
      </div>
      {!server.is_managed && (
        <span className="badge badge-warn" title="Сервер не подготовлен">
          не готов
        </span>
      )}
    </label>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Блок действий: install / remove / update по выбранным серверам
// ───────────────────────────────────────────────────────────────────────────

/**
 * Панель массовых действий над пакетами выбранных серверов. Видна только
 * носителю права `manage_packages`. Принимает список пакетов (через пробел),
 * кнопки Install / Remove / Update; Remove и Update идут с подтверждением
 * (деструктив). После dispatch'а показывает per-server исходы и поллит итог
 * каждой задачи до терминала.
 */
function PackageActionPanel({
  serverIds,
  servers,
}: {
  serverIds: string[];
  servers: Server[];
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

  async function dispatch(action: PackagesBulkActionKind) {
    if (running) return;
    if (serverIds.length === 0) {
      setErr("Выберите хотя бы один сервер.");
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
            ? `Удалить ${pkgText} на ${serverIds.length} серверах? Операция необратима.`
            : `Обновить ${pkgText} на ${serverIds.length} серверах?`,
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
      const res = await packagesBulkAction({
        server_ids: serverIds,
        action,
        ...(packages.length ? { packages } : {}),
      });
      if (!aliveRef.current) return;
      const init: ActionServerState[] = res.results.map((r) => ({
        serverId: r.server_id,
        hostname: r.hostname ?? hostnameOf(r.server_id),
        displayName:
          servers.find((s) => s.id === r.server_id)?.display_name ?? null,
        status: r.status,
        taskId: r.task_id ?? null,
        taskStatus: null,
        polling: r.status === "ok" && !!r.task_id,
        error: null,
      }));
      setStates(init);
      toast.success(
        `${ACTION_LABEL[action]}: задач поставлено ${res.dispatched} из ${res.requested}`,
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
        Действия с пакетами ({serverIds.length} серв.)
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
        <button
          type="button"
          className="btn btn-sm flex items-center justify-center gap-1"
          onClick={() => dispatch("install")}
          disabled={busy || serverIds.length === 0}
          title="Установить пакеты"
        >
          <Download className="w-3.5 h-3.5" /> Install
        </button>
        <button
          type="button"
          className="btn btn-sm btn-danger flex items-center justify-center gap-1"
          onClick={() => dispatch("remove")}
          disabled={busy || serverIds.length === 0}
          title="Удалить пакеты"
        >
          <Trash2 className="w-3.5 h-3.5" /> Remove
        </button>
        <button
          type="button"
          className="btn btn-sm flex items-center justify-center gap-1"
          onClick={() => dispatch("update")}
          disabled={busy || serverIds.length === 0}
          title="Обновить пакеты (пусто = upgrade всех)"
        >
          <ArrowUpCircle className="w-3.5 h-3.5" /> Update
        </button>
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
              {actionLabel} — исходы по серверам:
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
        className="truncate flex-1"
        title={`${state.hostname} · ${state.serverId}`}
      >
        {state.displayName ?? state.hostname}
      </span>
      <span className={`badge${kind ? ` badge-${kind}` : ""}`}>
        {ACTION_STATUS_LABEL[state.status] ?? state.status}
      </span>
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
  osLabel,
}: {
  states: ServerState[];
  orientation: Orientation;
  onOrientation: (o: Orientation) => void;
  anyPolling: boolean;
  dispatchErr: string | null;
  pattern: string;
  osLabel: (id: string | null) => string;
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
        server_id: s.serverId,
        hostname: s.hostname,
        display_name: s.displayName,
        os_version_id: s.osVersionId,
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
        (s) => `${s.displayName ?? s.hostname} (${osLabel(s.osVersionId)})`,
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
            {hasData && <> · серверов: {states.length}</>}
            {hasPackages && <> · уникальных пакетов: {packageNames.length}</>}
          </div>
        </div>
        {hasData && (
          <>
            <div className="flex items-center gap-1 surface-2 border border-token rounded p-0.5">
              <button
                type="button"
                className={`btn btn-sm flex items-center gap-1 ${orientation === "packages-rows" ? "btn-primary" : "btn-ghost"}`}
                onClick={() => onOrientation("packages-rows")}
                title="Строки = пакеты, столбцы = серверы"
              >
                <Rows3 className="w-3.5 h-3.5" /> пакеты × серверы
              </button>
              <button
                type="button"
                className={`btn btn-sm flex items-center gap-1 ${orientation === "servers-rows" ? "btn-primary" : "btn-ghost"}`}
                onClick={() => onOrientation("servers-rows")}
                title="Строки = серверы, столбцы = пакеты"
              >
                <Columns3 className="w-3.5 h-3.5" /> серверы × пакеты
              </button>
            </div>
            <button
              type="button"
              className="btn btn-sm flex items-center gap-1"
              onClick={exportJson}
              title="Экспорт текущей таблицы в JSON"
            >
              <Download className="w-3.5 h-3.5" /> JSON
            </button>
            <button
              type="button"
              className="btn btn-sm flex items-center gap-1"
              onClick={exportCsv}
              title="Экспорт текущей таблицы в CSV"
            >
              <Download className="w-3.5 h-3.5" /> CSV
            </button>
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
              Выберите серверы слева, задайте pattern и нажмите «Запросить».
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

            <StatusLegend states={states} osLabel={osLabel} />

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
                    osLabel={osLabel}
                  />
                ) : (
                  <ServersByRows
                    states={states}
                    packageNames={packageNames}
                    cell={cell}
                    osLabel={osLabel}
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

/** Шапка-легенда: статус каждого сервера в наборе. */
function StatusLegend({
  states,
  osLabel,
}: {
  states: ServerState[];
  osLabel: (id: string | null) => string;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {states.map((s) => {
        const kind = STATUS_KIND[s.status] ?? "";
        return (
          <div
            key={s.serverId}
            className="surface-2 border border-token rounded px-2 py-1 text-[11px] flex items-center gap-2"
            title={`${s.hostname} · ОС: ${osLabel(s.osVersionId)}`}
          >
            <span className="flex flex-col leading-tight min-w-0">
              <span className="truncate max-w-[160px]">
                {s.displayName ?? s.hostname}
              </span>
              {s.displayName && (
                <span className="mono text-[10px] text-dim truncate max-w-[160px]">
                  {s.hostname}
                </span>
              )}
            </span>
            <span className={`badge${kind ? ` badge-${kind}` : ""}`}>
              {STATUS_LABEL[s.status] ?? s.status}
            </span>
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

/** Режим A: строки = пакеты, столбцы = серверы. */
function PackagesByRows({
  states,
  packageNames,
  cell,
  osLabel,
}: {
  states: ServerState[];
  packageNames: string[];
  cell: (serverId: string, pkg: string) => string;
  osLabel: (id: string | null) => string;
}) {
  return (
    <div className="surface-2 border border-token rounded overflow-auto">
      <table className="text-sm border-collapse">
        <thead>
          <tr className="text-[11px] uppercase text-dim border-b border-token">
            <th className="text-left px-3 py-2 font-medium sticky left-0 surface-2 z-10">
              package
            </th>
            {states.map((s) => (
              <th
                key={s.serverId}
                className="text-left px-3 py-2 font-medium whitespace-nowrap"
                title={s.serverId}
              >
                <div>{s.displayName ?? s.hostname}</div>
                {s.displayName && (
                  <div className="mono text-dim font-normal normal-case">
                    {s.hostname}
                  </div>
                )}
                <div className="text-dim normal-case font-normal">
                  ОС: {osLabel(s.osVersionId)}
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

/** Режим B: строки = серверы, столбцы = пакеты (транспонированный). */
function ServersByRows({
  states,
  packageNames,
  cell,
  osLabel,
}: {
  states: ServerState[];
  packageNames: string[];
  cell: (serverId: string, pkg: string) => string;
  osLabel: (id: string | null) => string;
}) {
  return (
    <div className="surface-2 border border-token rounded overflow-auto">
      <table className="text-sm border-collapse">
        <thead>
          <tr className="text-[11px] uppercase text-dim border-b border-token">
            <th className="text-left px-3 py-2 font-medium sticky left-0 surface-2 z-10">
              server
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
                <div className="text-xs font-medium">
                  {s.displayName ?? s.hostname}
                </div>
                {s.displayName && (
                  <div className="mono text-[10px] text-dim">{s.hostname}</div>
                )}
                <div className="text-[10px] text-dim">
                  ОС: {osLabel(s.osVersionId)}
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
