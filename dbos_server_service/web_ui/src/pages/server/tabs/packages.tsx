/**
 * Packages-вкладка карточки сервера.
 *
 * Probe запускает live SSH-задачу через worker. Ответ endpoint'а — только
 * `{task_id, status}` (HTTP 202), сам список пакетов уезжает в `task.result`
 * и опрашивается отдельным запросом (`GET /tasks/{id}`). После dispatch'а
 * поллим task-row каждые ~3с до терминального статуса, дотягиваем
 * `result.packages` и рендерим таблицу `name / version / arch`.
 *
 * RBAC: probe доступен `server.operator`+ и dep_admin'у своего dept'а.
 * account_admin / logging_admin закрыты от server_service целиком — страница
 * /server для них не рендерится. Backend перепроверит ещё раз — клиентский
 * gate только прячет заведомо лишнюю кнопку.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Package, RefreshCw, AlertCircle } from "lucide-react";
import { installedPackagesProbe, getTask } from "@/api/server/misc";
import { listAccounts } from "@/api/server/accounts";
import { useQuery } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { isDepAdmin } from "@/lib/rbac";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  Server,
  ServerAccount,
  TaskRead,
} from "@/api/server/types";

const PACKAGES_POLL_MS = 3_000;

function isTerminal(status: string): boolean {
  return (
    status === "succeeded" || status === "failed" || status === "cancelled"
  );
}

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
}

interface PackageRow {
  name: string;
  version: string;
  arch?: string | null;
}

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

/**
 * Аккаунты сервера, видимые текущей persona (грубый client-side фильтр — тот
 * же контракт, что в `tabs/manage.tsx` / `tabs/console.tsx`). Backend
 * перепроверит при fetch'е пароля; здесь только UX, чтобы picker не показывал
 * заведомо недоступные строки.
 */
function filterAccessibleAccounts(
  accounts: ServerAccount[],
  persona: ReturnType<typeof usePersona>["persona"],
): ServerAccount[] {
  if (persona.service_roles.server === "admin") return accounts;
  if (
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "operator" ||
    persona.service_roles.server === "reader"
  ) {
    if (!persona.dept_id) return [];
    return accounts.filter((a) => a.department_id === persona.dept_id);
  }
  return [];
}

export function PackagesTab({ serverId, server }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const [pattern, setPattern] = useState("");
  const [pending, setPending] = useState(false);
  const [lastTaskId, setLastTaskId] = useState<string | null>(null);
  const [lastStatus, setLastStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [packages, setPackages] = useState<PackageRow[]>([]);
  const [polling, setPolling] = useState(false);

  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const allowed = canProbe(persona, server);

  // Аккаунты сервера — нужны probe'у на неуправляемом сервере: worker заходит
  // под self-сессией по паролю аккаунта. Управляемый сервер ходит по ключу —
  // picker тогда не обязателен (backend сам None'ит account_id).
  const needsAccount = !!server && !server.is_managed && allowed;
  const accountsQ = useQuery(
    () => listAccounts({ server_id: serverId, limit: 200 }),
    [serverId],
    { enabled: needsAccount },
  );
  const accounts = useMemo<ServerAccount[]>(() => {
    const data = accountsQ.data as
      | OffsetPaginatedResponse<ServerAccount>
      | CursorPaginatedResponse<ServerAccount>
      | undefined;
    return filterAccessibleAccounts(data?.items ?? [], persona);
  }, [accountsQ.data, persona]);
  const [accountId, setAccountId] = useState("");
  // account_id шлём только на неуправляемом сервере; на managed worker идёт
  // по ключу, передавать пусто.
  const probeAccountId =
    server && !server.is_managed && accountId ? accountId : undefined;

  // Поллинг task-row до терминального статуса: тянем result.packages.
  useEffect(() => {
    if (!lastTaskId || !polling) return;
    let stopped = false;
    const tick = () => {
      getTask(lastTaskId)
        .then((t) => {
          if (stopped || !aliveRef.current) return;
          setLastStatus(t.status);
          if (isTerminal(t.status)) {
            setPolling(false);
            setPackages(extractPackages(t.result));
            if (t.status === "failed") {
              setErr(t.last_error ?? "Probe завершился ошибкой");
            }
          }
        })
        .catch((e: unknown) => {
          if (stopped || !aliveRef.current) return;
          setPolling(false);
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

  async function handleProbe() {
    if (pending || !allowed) return;
    setPending(true);
    setErr(null);
    setPackages([]);
    try {
      const res = await installedPackagesProbe(
        serverId,
        pattern.trim() ? { pattern: pattern.trim() } : undefined,
        probeAccountId ? { account_id: probeAccountId } : {},
      );
      if (!aliveRef.current) return;
      setLastTaskId(res.task_id);
      setLastStatus(res.status);
      setPolling(true);
      toast.success(`Probe запущен (task ${res.task_id})`);
    } catch (e) {
      const msg = apiErrMsg(e, "Probe не запустился");
      if (aliveRef.current) setErr(msg);
      toast.error(msg);
    } finally {
      if (aliveRef.current) setPending(false);
    }
  }

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
              shell-glob'у. Сама проба обычно идёт <b>десятки секунд</b> —
              endpoint отдаёт <span className="mono">task_id</span> сразу,
              реальный список появится в результатах задачи.
            </div>
          </div>
          {allowed && (
            <button
              className="btn btn-primary flex items-center gap-2"
              onClick={handleProbe}
              disabled={pending}
              title="Запустить probe установленных пакетов"
            >
              <RefreshCw
                className={`w-4 h-4 ${pending ? "animate-spin" : ""}`}
              />
              {pending ? "Запускаем…" : "Probe installed packages"}
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
            disabled={pending || !allowed}
          />
          <span className="text-[11px] text-dim">
            пусто → `*` (все пакеты)
          </span>
        </div>

        {needsAccount && (
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <label className="text-xs text-dim">SSH-аккаунт для probe</label>
            <select
              className="surface-2 border border-token rounded px-2 py-1 text-sm"
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              disabled={pending || accountsQ.loading}
            >
              <option value="">— дефолтный аккаунт сервера —</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.login}
                  {a.has_sudo ? " (sudo)" : ""}
                  {a.source === "discovered" ? " · discovered" : ""}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-dim">
              {accountsQ.loading
                ? "загружаем…"
                : accounts.length === 0
                  ? "нет привязанных аккаунтов — probe вернёт 422"
                  : "пусто → первый аккаунт с паролем"}
            </span>
          </div>
        )}

        {!allowed && (
          <div className="mt-3 text-[11px] text-dim italic">
            Нет прав на запуск probe (нужна роль server.operator+ или
            dep_admin своего департамента).
          </div>
        )}
      </div>

      {err && (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">{err}</div>
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
                  Список пуст. Запусти probe и подожди, пока worker закроет
                  задачу.
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
