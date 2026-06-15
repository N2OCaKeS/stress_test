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
import { useEffect, useMemo, useRef, useState } from "react";
import { Package, RefreshCw, AlertCircle } from "lucide-react";
import { installedPackagesProbe, getTask } from "@/api/server/misc";
import { getServer, prepareServer } from "@/api/server/servers";
import { listAccounts } from "@/api/server/accounts";
import { ApiError, apiErrMsg } from "@/api/client";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { isDepAdmin } from "@/lib/rbac";
import { isTerminalTaskStatus } from "@/api/server/types";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  Server,
  ServerAccount,
  TaskRead,
} from "@/api/server/types";
import { filterAccessibleAccounts } from "@/pages/server/_serverShared";

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

export function PackagesTab({ server, onServerUpdated }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const [pattern, setPattern] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<string | null>(null);
  const [lastTaskId, setLastTaskId] = useState<string | null>(null);
  const [lastStatus, setLastStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [packages, setPackages] = useState<PackageRow[]>([]);
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
    <div className="p-5 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-start gap-3 flex-wrap">
          <div className="flex-1 min-w-[260px]">
            <div className="text-sm font-medium mb-1 flex items-center gap-2">
              <Package className="w-4 h-4 text-accent" />
              Установленные пакеты
            </div>
            <div className="text-xs text-dim">
              Live SSH-probe через worker: `dpkg-query` / `rpm -qa` по
              shell-glob'у. Если сервер ещё не подготовлен, prepare запустится
              автоматически под sudo-аккаунтом сервера. Сама проба обычно идёт{" "}
              <b>десятки секунд</b> — список появится после закрытия задачи.
            </div>
          </div>
          {allowed && (
            <button
              className="btn btn-primary flex items-center gap-2"
              onClick={() => handleProbe(false)}
              disabled={busy || !view}
              title="Получить список установленных пакетов"
            >
              <RefreshCw className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} />
              {buttonLabel}
            </button>
          )}
        </div>

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

        {!allowed && (
          <div className="mt-3 text-[11px] text-dim italic">
            Нет прав на запуск probe (нужна роль server.operator+ или
            dep_admin своего департамента).
          </div>
        )}
      </div>

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
              <button
                className="btn btn-ghost mt-2 flex items-center gap-1 text-xs"
                onClick={() => handleProbe(true)}
                disabled={busy}
                title="Повторно подготовить сервер и получить пакеты"
              >
                <RefreshCw className="w-3.5 h-3.5" /> Повторить prepare и пакеты
              </button>
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
                <span
                  className={`badge ${
                    lastStatus === "succeeded"
                      ? "badge-ok"
                      : lastStatus === "failed"
                        ? "badge-danger"
                        : "badge-warn"
                  }`}
                >
                  {lastStatus}
                </span>
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

      <div className="surface-2 border border-token rounded overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] uppercase text-dim border-b border-token">
              <th className="text-left px-3 py-2 font-medium">name</th>
              <th className="text-left px-3 py-2 font-medium">version</th>
              <th className="text-left px-3 py-2 font-medium">arch</th>
            </tr>
          </thead>
          <tbody>
            {packages.length === 0 ? (
              <tr>
                <td
                  colSpan={3}
                  className="px-3 py-4 text-xs text-dim text-center"
                >
                  Список пуст. Нажми «Получить пакеты» и подожди, пока worker
                  закроет задачу.
                </td>
              </tr>
            ) : (
              packages.map((p) => (
                <tr
                  key={`${p.name}-${p.version}-${p.arch ?? ""}`}
                  className="border-b border-token last:border-b-0"
                >
                  <td className="px-3 py-1.5 mono text-xs">{p.name}</td>
                  <td className="px-3 py-1.5 mono text-xs">{p.version}</td>
                  <td className="px-3 py-1.5 mono text-xs text-dim">
                    {p.arch ?? "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
