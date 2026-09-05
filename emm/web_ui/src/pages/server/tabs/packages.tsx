/**
 * Packages-вкладка карточки сервера.
 *
 * Probe запускает live SSH-задачу через worker. Ответ endpoint'а — только
 * `{task_id, status}` (HTTP 202), сам список пакетов уезжает в `task.result`
 * и опрашивается отдельным запросом (`GET /tasks/{id}`). После dispatch'а
 * поллим task-row каждые ~3с до терминального статуса, дотягиваем
 * `result.packages` и рендерим таблицу `name / version / arch`.
 *
 * Probe ходит по SSH под управляющим ключом — сервер обязан быть подготовлен
 * (`is_managed`, через prepare), иначе backend отбивает 409 PREPARE_REQUIRED.
 * Выбора аккаунта в этом потоке нет: если сервер не подготовлен, вкладка сама
 * находит sudo-аккаунт сервера, гоняет prepare с его `account_id`, дожидается
 * завершения и повторяет probe. Пользователь видит прогресс по шагам.
 *
 * RBAC: probe доступен `server.operator`+ и dep_admin'у своего dept'а.
 * account_admin / logging_admin закрыты от server_service целиком — страница
 * /server для них не рендерится. Backend перепроверит ещё раз — клиентский
 * gate только прячет заведомо лишнюю кнопку. Account-режим prepare на бэке
 * дополнительно требует `view_password`; если его нет — приходит 403, которую
 * показываем человекочитаемо.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Package,
  RefreshCw,
  AlertCircle,
  History,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import {
  installedPackagesProbe,
  getTask,
  getPackageHistory,
} from "@/api/server/misc";
import { getServer, prepareServer } from "@/api/server/servers";
import { listAccounts } from "@/api/server/accounts";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { isDepAdmin } from "@/lib/rbac";
import { isTerminalTaskStatus } from "@/api/server/types";
import { formatMskShort } from "@/lib/datetime";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  PackageHistoryEntry,
  Server,
  ServerAccount,
  TaskRead,
} from "@/api/server/types";
import { filterAccessibleAccounts } from "@/pages/server/_serverShared";
import { PackagesTable, type PackageItem } from "@/components/entity/PackagesTable";
import { listVmPackages, getVmPackageHistory } from "@/api/server/vms";
import type { Vm, VmPackagesResponse } from "@/api/server/vms";
import { mockVmPackages, mockVmPackageHistory } from "@/mocks/vm";
import type { PaginatedList } from "@/api/auth/users";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import type { EntityRef } from "./_entity";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";

const HISTORY_PAGE_SIZE = 20;

const PACKAGES_POLL_MS = 3_000;
const PREPARE_POLL_MS = 3_000;
// Prepare идёт по SSH (bootstrap-цикл), может занять минуты. Не ждём вечно —
// рвём поллинг с понятным сообщением, чтобы спиннер не висел навсегда.
const PREPARE_TIMEOUT_MS = 5 * 60_000;

/** Достаёт `packages` из произвольного `task.result` (best-effort). */
function extractPackages(result: TaskRead["result"]): PackageRow[] {
  if (!result || typeof result !== "object") return [];
  const raw = (result as Record<string, unknown>).packages;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((p): PackageRow | null => {
      if (!p || typeof p !== "object") return null;
      const obj = p as Record<string, unknown>;
      if (typeof obj.name !== "string") return null;
      return {
        name: obj.name,
        version: typeof obj.version === "string" ? obj.version : "",
        arch: typeof obj.arch === "string" ? obj.arch : null,
      };
    })
    .filter((p): p is PackageRow => p !== null);
}

interface Props {
  entity?: EntityRef;
  serverId: string;
  server?: Server;
  onServerUpdated?: (next: Server) => void;
}

interface PackageRow {
  name: string;
  version: string;
  arch?: string | null;
}

/** Фаза потока — для прогресс-подписи и блокировки кнопки. */
type Phase = "idle" | "preparing" | "probing";

function canProbe(
  persona: ReturnType<typeof usePersona>["persona"],
  server: Server | undefined,
): boolean {
  if (persona.service_roles.server === "admin") return true;
  if (persona.service_roles.server === "operator") return true;
  if (
    isDepAdmin(persona) &&
    server &&
    persona.dept_id === server.department_id
  ) {
    return true;
  }
  return false;
}

/** SSH-ошибка аутентификации в `last_error` — повод предложить re-prepare. */
function looksLikeAuthFailure(lastError: string | null | undefined): boolean {
  if (!lastError) return false;
  const s = lastError.toLowerCase();
  return (
    s.includes("auth") ||
    s.includes("permission denied") ||
    s.includes("password") ||
    s.includes("publickey")
  );
}

export function PackagesTab({ entity, serverId, server, onServerUpdated }: Props) {
  if (entity?.kind === "vm") {
    return <VmPackagesSection vm={entity.vm} mock={entity.mock} />;
  }
  return (
    <ServerPackagesTab
      serverId={serverId}
      server={server}
      onServerUpdated={onServerUpdated}
    />
  );
}

/** Кнопка «Обновить/Запросить» в шапке панели пакетов. */
interface PackagesPanelAction {
  label: string;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
  /** Скрыть кнопку целиком (нет прав). */
  hidden?: boolean;
  /** Акцентная кнопка (`btn-primary`) вместо нейтральной. */
  primary?: boolean;
  title?: string;
}

/**
 * Общая презентационная панель пакетов для сервера и ВМ: карточка с шапкой
 * (иконка + заголовок + счётчик + кнопка запроса), описанием и опциональными
 * слотами под доп. контролы/баннеры, плюс таблица `PackagesTable` со всеми её
 * состояниями. Данные и колбэк запроса приходят снаружи — сервер кормит
 * live-probe, ВМ отдаёт сохранённый срез гостя.
 */
function PackagesPanel({
  title,
  count,
  description,
  action,
  controls,
  note,
  banners,
  table,
  footer,
}: {
  title: string;
  count?: number;
  description?: ReactNode;
  action?: PackagesPanelAction;
  controls?: ReactNode;
  note?: ReactNode;
  banners?: ReactNode;
  table: {
    items: PackageItem[];
    filter: string;
    onFilter: (v: string) => void;
    loading?: boolean;
    error?: unknown;
    onRetry?: () => void;
    emptyText?: string;
  };
  footer?: ReactNode;
}) {
  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-start gap-3 flex-wrap">
          <div className="flex-1 min-w-[260px]">
            <h3 className="text-sm font-medium mb-1 flex items-center gap-2">
              <Package className="w-4 h-4 text-accent" />
              {title}
              {count != null && (
                <span className="text-xs text-dim font-normal">({count})</span>
              )}
            </h3>
            {description && (
              <div className="text-xs text-dim">{description}</div>
            )}
          </div>
          {action && !action.hidden && (
            <Button
              variant={action.primary ? "primary" : "default"}
              className="flex items-center gap-2"
              onClick={action.onClick}
              disabled={action.disabled}
              title={action.title}
            >
              <RefreshCw
                className={`w-4 h-4 ${action.busy ? "animate-spin" : ""}`}
              />
              {action.label}
            </Button>
          )}
        </div>
        {controls}
        {note}
      </div>

      {banners}

      <PackagesTable
        items={table.items}
        filter={table.filter}
        onFilter={table.onFilter}
        loading={table.loading}
        error={table.error}
        onRetry={table.onRetry}
        emptyText={table.emptyText}
      />

      {footer}
    </div>
  );
}

function ServerPackagesTab({ serverId, server, onServerUpdated }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const [pattern, setPattern] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<string | null>(null);
  const [lastTaskId, setLastTaskId] = useState<string | null>(null);
  const [lastStatus, setLastStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [packages, setPackages] = useState<PackageRow[]>([]);
  const [filter, setFilter] = useState("");
  const [polling, setPolling] = useState(false);
  // Probe на prepared-сервере упал с auth-ошибкой → возможно протух пароль
  // управляющего пользователя. Предлагаем повторно прогнать prepare.
  const [offerReprepare, setOfferReprepare] = useState(false);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  // Локальная копия сервера: после успешного prepare сервер становится
  // managed, нам нужен свежий `is_managed` для повторного probe и чтобы
  // соседние вкладки увидели обновление.
  const [current, setCurrent] = useState<Server | undefined>(server);
  useEffect(() => {
    setCurrent(server);
  }, [server]);
  const view = current ?? server;

  const allowed = canProbe(persona, view);
  const busy = phase !== "idle";

  // Поллинг probe-task'и до терминального статуса: тянем result.packages.
  useEffect(() => {
    if (!lastTaskId || !polling) return;
    let stopped = false;
    const tick = () => {
      getTask(lastTaskId)
        .then((t) => {
          if (stopped || !aliveRef.current) return;
          setLastStatus(t.status);
          if (isTerminalTaskStatus(t.status)) {
            setPolling(false);
            setPhase("idle");
            setProgress(null);
            setPackages(extractPackages(t.result));
            if (t.status === "failed") {
              setErr(t.last_error ?? "Probe завершился ошибкой");
              if (looksLikeAuthFailure(t.last_error)) {
                setOfferReprepare(true);
              }
            }
          }
        })
        .catch((e: unknown) => {
          if (stopped || !aliveRef.current) return;
          setPolling(false);
          setPhase("idle");
          setProgress(null);
          setErr(apiErrMsg(e, "Не удалось прочитать результат задачи"));
        });
    };
    tick();
    const id = window.setInterval(tick, PACKAGES_POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [lastTaskId, polling]);

  /** Поллит произвольную task'у до терминала; резолвится финальным TaskRead. */
  function waitForTask(taskId: string, timeoutMs: number): Promise<TaskRead> {
    return new Promise<TaskRead>((resolve, reject) => {
      const started = Date.now();
      const poll = () => {
        if (!aliveRef.current) {
          reject(new Error("cancelled"));
          return;
        }
        getTask(taskId)
          .then((t) => {
            if (!aliveRef.current) {
              reject(new Error("cancelled"));
              return;
            }
            if (isTerminalTaskStatus(t.status)) {
              resolve(t);
              return;
            }
            if (Date.now() - started > timeoutMs) {
              reject(new Error("timeout"));
              return;
            }
            window.setTimeout(poll, PREPARE_POLL_MS);
          })
          .catch(reject);
      };
      poll();
    });
  }

  /**
   * Находит sudo-аккаунт сервера для account-режима prepare. Берёт первый
   * доступный текущей persona аккаунт с `has_sudo`. Возвращает `null`, если
   * подходящего нет.
   */
  async function findSudoAccount(srv: Server): Promise<ServerAccount | null> {
    const data = (await listAccounts({ server_id: srv.id, limit: 200 })) as
      | OffsetPaginatedResponse<ServerAccount>
      | CursorPaginatedResponse<ServerAccount>;
    const accessible = filterAccessibleAccounts(data.items ?? [], persona);
    return accessible.find((a) => a.has_sudo && a.is_active) ?? null;
  }

  /**
   * Авто-prepare: ищет sudo-аккаунт, ставит prepare с его account_id, ждёт
   * завершения и возвращает свежий (managed) сервер. Бросает Error с готовым
   * человекочитаемым сообщением — caller покажет его в alert'е.
   */
  async function autoPrepare(srv: Server): Promise<Server> {
    setProgress("Сервер не подготовлен — ищу sudo-аккаунт для prepare…");
    const account = await findSudoAccount(srv);
    if (!account) {
      throw new Error(
        "На сервере нет привязанного sudo-аккаунта для prepare. " +
          "Заведите аккаунт с sudo во вкладке «Аккаунты» или подготовьте " +
          "сервер вручную во вкладке «Управление».",
      );
    }

    setProgress(
      `Запускаю prepare под аккаунтом ${account.login} (sudo)…`,
    );
    let dispatched;
    try {
      dispatched = await prepareServer(srv.id, { account_id: account.id });
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        throw new Error(
          "Недостаточно прав для prepare в account-режиме " +
            "(нужно право на чтение пароля аккаунта). Обратитесь к " +
            "администратору департамента.",
        );
      }
      throw new Error(apiErrMsg(e, "Не удалось запустить prepare"));
    }

    setProgress("Идёт prepare сервера… (обычно занимает до пары минут)");
    let task: TaskRead;
    try {
      task = await waitForTask(dispatched.task_id, PREPARE_TIMEOUT_MS);
    } catch (e) {
      if (e instanceof Error && e.message === "timeout") {
        throw new Error(
          "Prepare идёт дольше обычного. Проверьте статус задачи во " +
            "вкладке «Управление» и повторите получение пакетов позже.",
        );
      }
      throw new Error(apiErrMsg(e, "Не удалось дождаться prepare"));
    }
    if (task.status !== "succeeded") {
      throw new Error(
        task.last_error ??
          "Prepare завершился неуспешно. Проверьте bootstrap-креды аккаунта.",
      );
    }

    // Сервер помечается managed worker-callback'ом; перечитываем карточку,
    // чтобы получить актуальный is_managed и поднять его наверх.
    setProgress("Prepare завершён — обновляю карточку сервера…");
    const refreshed = await getServer(srv.id);
    setCurrent(refreshed);
    onServerUpdated?.(refreshed);
    return refreshed;
  }

  /** Диспатчит probe и включает поллинг result'а. */
  async function dispatchProbe(srv: Server): Promise<void> {
    setProgress("Получаю список пакетов…");
    const res = await installedPackagesProbe(
      srv.id,
      pattern.trim() ? { pattern: pattern.trim() } : undefined,
    );
    if (!aliveRef.current) return;
    setLastTaskId(res.task_id);
    setLastStatus(res.status);
    setPhase("probing");
    setPolling(true);
    toast.success(`Probe запущен (task ${res.task_id})`);
  }

  async function handleProbe(forcePrepare = false) {
    if (busy || !allowed || !view) return;
    setErr(null);
    setPackages([]);
    setOfferReprepare(false);
    setLastTaskId(null);
    setLastStatus(null);

    try {
      let srv = view;
      // Не подготовлен (или принудительное re-prepare после auth-сбоя) —
      // сперва прогоняем prepare.
      if (!srv.is_managed || forcePrepare) {
        setPhase("preparing");
        srv = await autoPrepare(srv);
      }

      try {
        setPhase("probing");
        await dispatchProbe(srv);
      } catch (e) {
        // Гонка: думали managed, а backend отбил PREPARE_REQUIRED. Один раз
        // авто-prepare'имся и повторяем probe.
        if (
          !forcePrepare &&
          e instanceof ApiError &&
          e.errorCode === "PREPARE_REQUIRED"
        ) {
          setPhase("preparing");
          const prepared = await autoPrepare(srv);
          setPhase("probing");
          await dispatchProbe(prepared);
        } else {
          throw e;
        }
      }
    } catch (e) {
      if (!aliveRef.current) return;
      const msg = e instanceof Error ? e.message : apiErrMsg(e, "Не удалось получить пакеты");
      setErr(msg);
      toast.error(msg);
      setPhase("idle");
      setProgress(null);
    }
  }

  const buttonLabel = useMemo(() => {
    if (phase === "preparing") return "Готовим сервер…";
    if (phase === "probing") return "Получаем пакеты…";
    return "Получить пакеты";
  }, [phase]);

  return (
    <PackagesPanel
      title="Установленные пакеты"
      description={
        <>
          Live SSH-probe через worker: `dpkg-query` / `rpm -qa` по shell-glob'у.
          Если сервер ещё не подготовлен, prepare запустится автоматически под
          sudo-аккаунтом сервера. Сама проба обычно идёт <b>десятки секунд</b> —
          список появится после закрытия задачи.
        </>
      }
      action={{
        label: buttonLabel,
        onClick: () => handleProbe(false),
        busy,
        disabled: busy || !view,
        hidden: !allowed,
        primary: true,
        title: "Получить список установленных пакетов",
      }}
      controls={
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <label className="text-xs text-dim">pattern (shell glob)</label>
          <input
            className="input mono text-xs"
            style={{ minWidth: 220 }}
            placeholder="* / linux-image* / *-dev"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            disabled={busy || !allowed}
          />
          <span className="text-[11px] text-dim">
            пусто → `*` (все пакеты)
          </span>
        </div>
      }
      note={
        !allowed ? (
          <div className="mt-3 text-[11px] text-dim italic">
            Нет прав на запуск probe (нужна роль server.operator+ или
            dep_admin своего департамента).
          </div>
        ) : null
      }
      banners={
        <>
          {progress && (
            <div className="surface-2 border border-token rounded p-3 text-xs flex items-center gap-2">
              <RefreshCw className="w-4 h-4 animate-spin text-accent" />
              <span>{progress}</span>
            </div>
          )}

          {err && (
            <div className="alert alert-danger flex items-start gap-2">
              <AlertCircle className="w-4 h-4 mt-0.5" />
              <div className="flex-1 text-xs">
                <div>{err}</div>
                {offerReprepare && allowed && (
                  <Button variant="ghost"
                    className="mt-2 flex items-center gap-1 text-xs"
                    onClick={() => handleProbe(true)}
                    disabled={busy}
                    title="Повторно подготовить сервер и получить пакеты"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> Повторить prepare и
                    пакеты
                  </Button>
                )}
              </div>
            </div>
          )}

          {lastTaskId && (
            <div className="surface-2 border border-token rounded p-3 text-xs flex flex-col gap-1">
              <div className="flex items-center gap-2 flex-wrap">
                Последний probe:{" "}
                <span className="mono">{lastTaskId}</span>
                {lastStatus && (
                  <>
                    {" · "}статус{" "}
                    <Badge
                      kind={
                        lastStatus === "succeeded"
                          ? "ok"
                          : lastStatus === "failed"
                            ? "danger"
                            : "warn"
                      }
                    >
                      {lastStatus}
                    </Badge>
                  </>
                )}
                {polling && (
                  <span className="flex items-center gap-1 text-dim">
                    <RefreshCw className="w-3 h-3 animate-spin" /> ждём worker…
                  </span>
                )}
              </div>
              {!polling && lastStatus === "succeeded" && (
                <div className="text-dim">
                  Найдено пакетов: <b>{packages.length}</b> (из{" "}
                  <span className="mono">task.result</span>).
                </div>
              )}
            </div>
          )}
        </>
      }
      table={{
        items: packages,
        filter,
        onFilter: setFilter,
        emptyText:
          "Список пуст. Нажми «Получить пакеты» и подожди, пока worker закроет задачу.",
      }}
      footer={
        <PackageHistorySection
          depKey={serverId}
          fetchPage={(q) => getPackageHistory(serverId, q)}
          hint="Прошлые probe'ы пакетов этого сервера — результат можно посмотреть, не запуская SSH-пробу заново. Глубина ограничена retention'ом worker'а."
          emptyText="Запросов пакетов по этому серверу ещё не было."
        />
      }
    />
  );
}

// ───────────────────────────────────────────────────────────────────────────
// История прошлых запросов пакетов по серверу
// ───────────────────────────────────────────────────────────────────────────

/** Человеко-читаемые подписи статусов запроса в истории. */
const HISTORY_STATUS_LABEL: Record<string, string> = {
  queued: "в очереди",
  running: "выполняется",
  succeeded: "готово",
  failed: "ошибка",
  cancelled: "отменён",
};

const HISTORY_STATUS_KIND: Record<string, "ok" | "warn" | "danger" | ""> = {
  queued: "warn",
  running: "warn",
  succeeded: "ok",
  failed: "danger",
  cancelled: "",
};

/** Паттерн(ы) запроса одной строкой: `patterns` приоритетнее одиночного `pattern`. */
function historyPatternLabel(entry: PackageHistoryEntry): string {
  if (entry.patterns && entry.patterns.length > 0) {
    return entry.patterns.join(" ");
  }
  return entry.pattern ?? "*";
}

/**
 * Раздел «История запросов пакетов»: список прошлых probe'ов сущности
 * (сервер / гость ВМ) DESC по времени, без повторного SSH. Клик по строке
 * разворачивает её результат (`packages`) из уже сохранённой задачи.
 * Незавершённые (`queued`/`running`) показываются с пометкой и без списка.
 * Источник страницы приходит снаружи (`fetchPage`) — так вкладка сервера и ВМ
 * делят одну вёрстку, различаясь только эндпоинтом и текстами.
 */
function PackageHistorySection({
  depKey,
  fetchPage,
  hint,
  emptyText,
}: {
  /** Ключ пересборки запроса (id сервера/ВМ) — идёт в deps useQuery. */
  depKey: string;
  fetchPage: (q: {
    limit: number;
    offset: number;
  }) => Promise<PaginatedList<PackageHistoryEntry>>;
  hint: ReactNode;
  emptyText: ReactNode;
}) {
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const historyQ = useQuery(
    () =>
      fetchPage({
        limit: HISTORY_PAGE_SIZE,
        offset: page * HISTORY_PAGE_SIZE,
      }),
    [depKey, page],
    { keepPreviousDataOnError: true },
  );

  const items = historyQ.data?.items ?? [];
  const total = historyQ.data?.total ?? 0;
  const totalKnown = historyQ.data?.totalKnown !== false;
  const pages = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
  const hasNext = totalKnown
    ? page + 1 < pages
    : items.length === HISTORY_PAGE_SIZE;

  function toggle(taskId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-1">
        <History className="w-4 h-4 text-accent" />
        <div className="text-sm font-medium">История запросов</div>
        <Button variant="ghost" size="sm"
          className="ml-auto flex items-center gap-1"
          onClick={() => historyQ.refetch()}
          disabled={historyQ.loading}
          title="Обновить историю"
        >
          <RefreshCw
            className={`w-3.5 h-3.5 ${historyQ.loading ? "animate-spin" : ""}`}
          />
          Обновить
        </Button>
      </div>
      <div className="text-xs text-dim mb-3">{hint}</div>

      {historyQ.error && (
        <div className="alert alert-danger flex items-start gap-2 mb-3">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(historyQ.error, "Историю не загрузить")}</div>
            <Button variant="ghost"
              className="mt-2"
              onClick={() => historyQ.refetch()}
            >
              Повторить
            </Button>
          </div>
        </div>
      )}

      {historyQ.loading && items.length === 0 ? (
        <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-dim py-4 text-center">{emptyText}</div>
      ) : (
        <div className="flex flex-col gap-1">
          {items.map((entry) => (
            <HistoryRow
              key={entry.task_id}
              entry={entry}
              open={expanded.has(entry.task_id)}
              onToggle={() => toggle(entry.task_id)}
            />
          ))}
        </div>
      )}

      {(hasNext || page > 0) && (
        <div className="flex items-center justify-between mt-3 text-xs text-dim">
          <span>
            {totalKnown ? (
              <>
                стр. {page + 1} из {pages} · всего {total}
              </>
            ) : (
              <>стр. {page + 1}</>
            )}
          </span>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || historyQ.loading}
            >
              Назад
            </Button>
            <Button variant="ghost" size="sm"
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNext || historyQ.loading}
            >
              Вперёд
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Одна строка истории: шапка (паттерн/время/инициатор/статус) + раскрытие. */
function HistoryRow({
  entry,
  open,
  onToggle,
}: {
  entry: PackageHistoryEntry;
  open: boolean;
  onToggle: () => void;
}) {
  const pending = entry.status === "queued" || entry.status === "running";
  const kind = HISTORY_STATUS_KIND[entry.status] ?? "";
  const packages = entry.packages ?? [];
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div className="surface-2 border border-token rounded">
      <button
        type="button"
        className="w-full text-left px-3 py-2 flex items-center gap-2"
        onClick={onToggle}
        aria-expanded={open}
      >
        <Chevron className="w-4 h-4 text-dim shrink-0" />
        <span className="mono text-xs truncate flex-1" title="запрошенный шаблон">
          {historyPatternLabel(entry)}
        </span>
        <Badge kind={kind || "neutral"} className="shrink-0">
          {HISTORY_STATUS_LABEL[entry.status] ?? entry.status}
        </Badge>
        {pending ? (
          <RefreshCw className="w-3 h-3 animate-spin text-dim shrink-0" />
        ) : (
          <span className="text-[11px] text-dim shrink-0 w-16 text-right">
            {entry.package_count ?? 0} пак.
          </span>
        )}
      </button>
      <div className="px-3 pb-1 -mt-1 text-[11px] text-dim flex flex-wrap items-center gap-x-3 gap-y-0.5">
        <span title="время постановки запроса">
          {formatMskShort(entry.requested_at)}
        </span>
        {entry.requested_by && (
          <span className="mono truncate" title="инициатор запроса">
            {entry.requested_by}
          </span>
        )}
      </div>

      {open && (
        <div className="border-t border-token px-3 py-2">
          {pending ? (
            <div className="text-[11px] text-dim italic">
              Запрос ещё выполняется — список пакетов появится после завершения
              задачи.
            </div>
          ) : entry.status === "failed" ? (
            <div className="text-[11px] text-danger">
              {entry.last_error ?? "Запрос завершился ошибкой."}
            </div>
          ) : packages.length === 0 ? (
            <div className="text-[11px] text-dim italic">
              Пакетов по этому запросу не найдено.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase text-dim border-b border-token">
                  <th className="text-left px-2 py-1 font-medium">Название</th>
                  <th className="text-left px-2 py-1 font-medium">Версия</th>
                </tr>
              </thead>
              <tbody>
                {packages.map((p) => (
                  <tr
                    key={`${p.name}-${p.version}`}
                    className="border-b border-token last:border-b-0"
                  >
                    <td className="px-2 py-1 mono text-xs">{p.name}</td>
                    <td className="px-2 py-1 mono text-xs text-dim">
                      {p.version || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Пакеты гостя ВМ
// ───────────────────────────────────────────────────────────────────────────

/**
 * Read-only список установленных в госте пакетов ВМ. По образцу серверной
 * вкладки, но без live-SSH-probe в самом GET: показываем последний снятый
 * воркером срез из `/vms/{id}/packages`. Кнопка «Обновить» ставит свежий probe
 * (`?refresh=true` → задача `vm.list_packages`), поллит её исход через
 * `useTaskOutcome` и по успешному закрытию подтягивает обновлённый срез.
 * В mock-режиме данные из `@/mocks/vm`.
 */
function VmPackagesSection({ vm, mock }: { vm: Vm; mock: boolean }) {
  const toast = useToast();
  const outcome = useTaskOutcome();
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState("");
  const [pattern, setPattern] = useState("");

  const pkgsQ = useQuery<VmPackagesResponse>(
    async () => {
      if (mock) return mockVmPackages(vm);
      return await listVmPackages(vm.id);
    },
    [vm.id, mock],
    { keepPreviousDataOnError: true },
  );
  const packages = useMemo(() => pkgsQ.data?.packages ?? [], [pkgsQ.data]);
  const syncedAt = pkgsQ.data?.synced_at ?? null;

  // Свежий probe пакетов закрылся успехом — воркер уже записал новый срез,
  // перечитываем сохранённый список, чтобы таблица показала актуальное.
  const trackedStatus = outcome.tracked?.status;
  const trackedPolling = outcome.tracked?.polling ?? false;
  useEffect(() => {
    if (trackedStatus === "succeeded" && !trackedPolling) {
      pkgsQ.refetch();
    }
  }, [trackedStatus, trackedPolling, pkgsQ.refetch]);

  async function refresh() {
    if (mock) {
      pkgsQ.refetch();
      return;
    }
    setRefreshing(true);
    outcome.reset();
    try {
      const res = await listVmPackages(vm.id, {
        refresh: true,
        pattern: pattern.trim() || undefined,
      });
      if (res.dispatched && res.task_id) {
        outcome.track("Сбор пакетов гостя", res.task_id, "queued");
        toast.info(
          "Запущен свежий сбор пакетов — список обновится после закрытия задачи.",
        );
      } else {
        // Бэкенд не задиспатчил новый probe — просто перечитаем сохранённый срез.
        pkgsQ.refetch();
        toast.info("Обновляю сохранённый список пакетов.");
      }
    } catch (e) {
      toast.error(pkgRefreshErrorMsg(e));
    } finally {
      setRefreshing(false);
    }
  }

  const busy = pkgsQ.loading || refreshing || trackedPolling;

  return (
    <PackagesPanel
      title="Пакеты"
      count={packages.length}
      description={
        <>
          Установленные в госте пакеты (`dpkg -l` / `rpm -qa`), последний снятый
          воркером срез
          {syncedAt ? ` (синк ${formatSnapDate(syncedAt)})` : ""}. Кнопка
          «Обновить» ставит свежий сбор через worker.
        </>
      }
      action={{
        label: "Обновить",
        onClick: refresh,
        busy,
        disabled: busy,
        title: "Поставить свежий probe и обновить список",
      }}
      controls={
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <label className="text-xs text-dim">pattern (shell glob)</label>
          <input
            className="input mono text-xs"
            style={{ minWidth: 220 }}
            placeholder="* / linux-image* / *-dev"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            disabled={busy}
          />
          <span className="text-[11px] text-dim">
            пусто → `*` (все пакеты)
          </span>
        </div>
      }
      banners={
        outcome.tracked && (
          <TaskOutcomeBanner
            outcome={outcome.tracked}
            label="Сбор пакетов гостя"
            successText="Свежий список пакетов снят — таблица обновлена."
          />
        )
      }
      table={{
        items: packages,
        filter,
        onFilter: setFilter,
        loading: pkgsQ.loading,
        error: pkgsQ.error,
        onRetry: () => pkgsQ.refetch(),
      }}
      footer={
        <PackageHistorySection
          depKey={vm.id}
          fetchPage={(q) =>
            mock
              ? Promise.resolve(mockVmPackageHistory(vm, q))
              : getVmPackageHistory(vm.id, q)
          }
          hint="Прошлые сборы пакетов гостя этой ВМ — результат можно посмотреть, не запуская probe заново. Глубина ограничена retention'ом worker'а."
          emptyText="Запросов пакетов по этой ВМ ещё не было."
        />
      }
    />
  );
}

/** Разбор 409-ошибок свежего probe пакетов ВМ в человекочитаемый текст. */
function pkgRefreshErrorMsg(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.errorCode === "VM_PREPARE_REQUIRED")
      return "ВМ не подготовлена (prepare) — свежий сбор пакетов недоступен.";
    if (e.errorCode === "VM_GUEST_IP_UNKNOWN")
      return "У ВМ нет известного IP гостя — свежий сбор пакетов недоступен.";
    if (e.errorCode === "HUB_UNAVAILABLE")
      return "Hub недоступен — свежий сбор пакетов недоступен.";
  }
  return apiErrMsg(e, "Не удалось запустить сбор пакетов");
}

/** Дата последнего синка пакетов ВМ в MSK, короткий формат. */
function formatSnapDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    dateStyle: "short",
    timeStyle: "short",
  });
}
