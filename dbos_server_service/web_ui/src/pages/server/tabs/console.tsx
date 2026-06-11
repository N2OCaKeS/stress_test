/**
 * Console-вкладка карточки сервера.
 *
 * UX: picker аккаунтов, на которые у текущего юзера есть read-grant, и
 * placeholder для SSH-сессии. Backend WebSocket-эндпоинта для консоли
 * (`WS /api/server/v1/servers/{id}/console`) пока нет — терминала не рисуем,
 * показываем заметку с описанием контракта, который нужен бэку.
 *
 * Когда бэкенд появится, обмен ожидается JSON-фреймами:
 *   client → server: `{type:'input', data:string}` | `{type:'resize', cols, rows}`
 *   server → client: `{type:'output', data:string}` | `{type:'info', ...}` | `{type:'error', data}`
 *
 * Терминал хочется через `xterm.js` (`@xterm/xterm` + `@xterm/addon-fit` +
 * `@xterm/addon-web-links`), но эти пакеты в `web_ui/package.json` пока не
 * подключены — поставить отдельной волной. Образец интеграции —
 * `dev_allta_app_demo:new_allta_app/frontend/src/components/ConsoleTab.tsx`.
 */
import { useMemo, useState } from "react";
import { AlertCircle, Lock, Terminal as TerminalIcon } from "lucide-react";
import { listAccounts } from "@/api/server/accounts";
import { useQuery } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { ApiError } from "@/api/client";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  Server,
  ServerAccount,
} from "@/api/server/types";

interface Props {
  serverId: string;
  server?: Server;
}

function apiErrMsg(e: unknown, fallback = "Ошибка"): string {
  if (e instanceof ApiError) return `${e.errorCode}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return fallback;
}

/**
 * Аккаунты, которые юзеру разрешено читать (= можно открыть SSH-сессию).
 *
 * RBAC в server_service action-based; чтобы не дёргать `/permissions` на
 * каждый аккаунт, мы делаем грубый, но честный клиент-side фильтр поверх
 * текущей persona — он отражает ту же логику, что и backend для action
 * `view` (и его деривата для консоли):
 *
 *   - `account_admin` (platform) и `server.admin` — все аккаунты;
 *   - `dep_admin` своего dept'а — все аккаунты из его dept'а;
 *   - regular user с `server.operator`/`server.reader` — только аккаунты
 *     его dept'а (cross-dep шаринг через `DeptGrant` пока в UI не виден,
 *     backend сам отрежет при попытке открыть сессию).
 *
 * Источник истины при реальной попытке подключения — backend (он сделает
 * полную проверку и вернёт 403, если grant'а нет). Этот фильтр — для UX,
 * чтобы юзер не видел в picker'е заведомо недоступные строки.
 */
function filterAccessible(
  accounts: ServerAccount[],
  persona: ReturnType<typeof usePersona>["persona"],
): ServerAccount[] {
  if (persona.platform_role === "account_admin") return accounts;
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

export function ConsoleTab({ serverId, server }: Props) {
  const { persona } = usePersona();
  const accountsQ = useQuery(
    () => listAccounts({ server_id: serverId, limit: 200 }),
    [serverId],
  );

  const accounts = useMemo<ServerAccount[]>(() => {
    const data = accountsQ.data as
      | OffsetPaginatedResponse<ServerAccount>
      | CursorPaginatedResponse<ServerAccount>
      | undefined;
    return data?.items ?? [];
  }, [accountsQ.data]);

  const accessible = useMemo(
    () => filterAccessible(accounts, persona),
    [accounts, persona],
  );

  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const selectedAccount =
    accessible.find((a) => a.id === selectedAccountId) ?? null;

  return (
    <div className="p-5 flex flex-col gap-4 max-w-3xl">
      <div>
        <div className="text-sm font-medium mb-1 flex items-center gap-2">
          <TerminalIcon className="w-4 h-4 text-accent" />
          SSH-консоль
        </div>
        <div className="text-xs text-dim">
          Подключение к серверу{" "}
          <span className="mono">
            {server?.display_name ?? server?.hostname ?? serverId}
          </span>{" "}
          под одной из доступных вам сервисных учёток.
        </div>
      </div>

      {accountsQ.loading && (
        <div className="text-xs text-dim">Загружаем аккаунты…</div>
      )}

      {accountsQ.error && (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(accountsQ.error, "Аккаунты не загрузились")}</div>
            <button
              className="btn btn-ghost mt-2"
              onClick={() => accountsQ.refetch()}
            >
              Повторить
            </button>
          </div>
        </div>
      )}

      {!accountsQ.loading && !accountsQ.error && accessible.length === 0 && (
        <div className="alert flex items-start gap-2">
          <Lock className="w-4 h-4 mt-0.5 text-dim" />
          <div className="flex-1 text-xs text-dim">
            На этом сервере нет аккаунтов, к которым у вас есть доступ.
            {accounts.length > 0 && (
              <>
                {" "}
                Всего привязано: {accounts.length}. Запросите grant у
                администратора сервиса или департамента.
              </>
            )}
          </div>
        </div>
      )}

      {accessible.length > 0 && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Аккаунт</span>
          <select
            className="surface-2 border border-token rounded px-2 py-1"
            value={selectedAccountId}
            onChange={(e) => setSelectedAccountId(e.target.value)}
          >
            <option value="">— выберите —</option>
            {accessible.map((a) => (
              <option key={a.id} value={a.id}>
                {a.login}
                {a.has_sudo ? " (sudo)" : ""}
                {a.source === "discovered" ? " · discovered" : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      <ConsolePlaceholder
        serverId={serverId}
        account={selectedAccount}
        ready={accessible.length > 0}
      />
    </div>
  );
}

/**
 * Заглушка терминала.
 *
 * Backend SSH-консоль ещё не реализована: в `server_service/src/api/v1/`
 * нет WebSocket-роутов и `/servers/{id}/console`. До появления контракта
 * мы не пытаемся открыть WebSocket — иначе любой клик по «Подключиться»
 * упрётся в 404/426 и собьёт UX. Когда endpoint появится, заменим этот
 * блок на интеграцию с `xterm.js` (см. шапку файла).
 */
function ConsolePlaceholder({
  serverId,
  account,
  ready,
}: {
  serverId: string;
  account: ServerAccount | null;
  ready: boolean;
}) {
  return (
    <div className="surface-2 border border-token rounded p-4 text-xs text-dim flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Lock className="w-4 h-4" />
        <span className="text-sm">Backend SSH-консоль ещё не реализована.</span>
      </div>
      <div>
        Когда в <span className="mono">server_service</span> появится endpoint{" "}
        <span className="mono">WS /api/server/v1/servers/{"{id}"}/console</span>,
        здесь поднимется xterm.js-сессия с выбранной учёткой. Сейчас вкладка
        отрабатывает только picker и фильтрацию по правам.
      </div>
      <div className="mono text-[11px]">
        server_id = {serverId}
        {account ? ` · account = ${account.login} (${account.id})` : ""}
      </div>
      <button
        className="btn btn-primary self-start mt-1"
        disabled={!ready || !account}
        onClick={() => {
          // intentional no-op: backend WS endpoint отсутствует. Когда появится,
          // заменить на открытие WebSocket к /api/server/v1/servers/{id}/console
          // с заголовком Authorization и query ?account_id=<...>.
        }}
        title={
          !ready
            ? "Нет доступных аккаунтов"
            : !account
              ? "Выберите аккаунт"
              : "Backend пока не поддерживает консоль"
        }
      >
        Подключиться
      </button>
    </div>
  );
}
