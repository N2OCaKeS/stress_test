/**
 * Страница /server/users — сводный список всех server_account'ов отдела в
 * 3-панельной раскладке (Shell: left + middle-list + right-workzone).
 *
 * Per-server аккаунты живут на вкладке «Аккаунты» карточки сервера; здесь —
 * единый список «все пользователи разом» по всем доступным серверам. Backend
 * не отдаёт общую выдачу аккаунтов (`GET /server-accounts` требует
 * обязательный `server_id`), поэтому страница сначала тянет список серверов
 * (`listServers`), затем веером дёргает `listAccounts` на каждый и сшивает
 * результат: один аккаунт может быть привязан к нескольким серверам, дубли по
 * `id` объединяются.
 *
 * Средняя панель — список аккаунтов с поиском/фильтром по серверу и сортом;
 * выбор строки кладёт `?id=<accountId>` в URL. Правая рабочая зона — карточка
 * выбранного аккаунта с управлением: reveal пароля, edit (PATCH
 * has_sudo/unix_groups/shell), ротация пароля (только БД), удаление и секция
 * «Серверы аккаунта» — per-server provision (useradd) / update_on_host /
 * deprovision (userdel) / bind / unbind по каждому привязанному серверу.
 * Те же операции доступны и на вкладке «Аккаунты» карточки сервера.
 *
 * account_admin / logging_* отрезаны от server-зоны backend'ом
 * (`PLATFORM_ADMIN_BUSINESS_DATA_DENIED`) — для них BlockedPane вместо мёртвой
 * страницы. dep_admin видит аккаунты серверов своего отдела; server.*-роли —
 * по матрице.
 */
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Search,
  Users,
  AlertCircle,
  User,
  KeyRound,
  Eye,
  EyeOff,
  Copy,
  Edit3,
  Trash2,
  RotateCw,
  ShieldCheck,
  Server as ServerIcon,
  Power,
  Unlink,
  Link2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import { fromBase64 } from "@/lib/base64";
import { formatMskShort } from "@/lib/datetime";
import { useServerMap, useUserLabel } from "@/lib/labels";
import { listServers } from "@/api/server/servers";
import * as accountsApi from "@/api/server/accounts";
import { isServerZoneBlocked } from "@/lib/rbac";
import type { Server, ServerAccount, ServerAccountUpdateRequest } from "@/api/server/types";

// Аккаунтов и серверов на отдел немного — одной страницы с запасом хватает,
// клиентский поиск/сорт идут по загруженному набору. Кап честно отражается в
// TruncationNotice.
const SERVER_LIMIT = 200;
const ACCOUNTS_PER_SERVER = 200;

// Зеркало `server_service/src/schemas/server_account.py`:
//   unix_groups — POSIX group name `^[a-z_][a-z0-9_-]{0,31}$`.
const POSIX_GROUP_RE = /^[a-z_][a-z0-9_-]{0,31}$/;

function validateUnixGroups(groups: string[]): string | null {
  for (const g of groups) {
    if (!POSIX_GROUP_RE.test(g)) {
      return `unix_groups: '${g}' не POSIX-имя (строчные, цифры, _ -, до 32 симв.)`;
    }
  }
  return null;
}

type SortMode = "login" | "server" | "rotated";

/**
 * Сводный аккаунт: сам `ServerAccount` плюс факт усечения хотя бы на одном
 * сервере (если у какого-то сервера аккаунтов больше, чем `ACCOUNTS_PER_SERVER`).
 */
interface AggregatedAccounts {
  accounts: ServerAccount[];
  /** Серверы, чью страницу аккаунтов backend усёк по лимиту. */
  truncatedServers: number;
  /** Серверы, чей список аккаунтов не загрузился (403/сеть) — счётчик. */
  failedServers: number;
}

/**
 * Веером тянет аккаунты по всем серверам и сшивает в один список без дублей.
 * Аккаунт, привязанный к N серверам, придёт в N выдачах — оставляем первую
 * копию (server_ids в карточке уже несёт полный список серверов).
 */
async function loadAllAccounts(serverIds: string[]): Promise<AggregatedAccounts> {
  const byId = new Map<string, ServerAccount>();
  let truncatedServers = 0;
  let failedServers = 0;
  const results = await Promise.allSettled(
    serverIds.map((sid) =>
      accountsApi.listAccounts({ server_id: sid, limit: ACCOUNTS_PER_SERVER }),
    ),
  );
  for (const r of results) {
    if (r.status === "rejected") {
      failedServers += 1;
      continue;
    }
    const data = r.value;
    for (const a of data.items) {
      if (!byId.has(a.id)) byId.set(a.id, a);
    }
    if ("total" in data && data.total > data.items.length) {
      truncatedServers += 1;
    }
  }
  return {
    accounts: [...byId.values()],
    truncatedServers,
    failedServers,
  };
}

export function ServerUsers() {
  const { persona } = usePersona();
  const zoneBlocked = isServerZoneBlocked(persona);
  const [params, setParams] = useSearchParams();

  const selectedId = params.get("id");

  // server.* / dep_admin → reveal по матрице (фактический грант view_password
  // проверяет backend). guest/reader без view_password получат null/403 —
  // кнопку держим доступной для operator+ как в карточке аккаунта.
  const isDepAdmin = persona.platform_role === "dep_admin";
  const serverRole = persona.service_roles.server;
  const canOperate =
    isDepAdmin || serverRole === "admin" || serverRole === "operator";
  // create/edit/delete учётки — только admin/dep_admin (как на вкладке
  // «Аккаунты» карточки сервера). Ротация и reveal — operator+.
  const canManage = isDepAdmin || serverRole === "admin";
  const canReveal = canOperate;

  const serverMap = useServerMap();

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortMode>("login");
  const [serverFilter, setServerFilter] = useState<string>("all");

  const serversQ = useQuery(
    () => listServers({ limit: SERVER_LIMIT }),
    [],
    { enabled: !zoneBlocked },
  );

  const servers = useMemo(() => serversQ.data?.items ?? [], [serversQ.data]);
  const serverTotal = serversQ.data?.total ?? servers.length;
  const serverIds = useMemo(() => servers.map((s) => s.id), [servers]);

  // Аккаунты тянем веером по серверам — пересобираем при смене набора серверов.
  const accountsQ = useQuery(
    () => loadAllAccounts(serverIds),
    [serverIds.join(",")],
    { enabled: !zoneBlocked && serverIds.length > 0 },
  );

  const allAccounts = useMemo(
    () => accountsQ.data?.accounts ?? [],
    [accountsQ.data],
  );

  const serverName = (id: string) => serverMap.get(id) ?? id;

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const matched = allAccounts.filter((a) => {
      if (serverFilter !== "all" && !a.server_ids.includes(serverFilter)) {
        return false;
      }
      if (!term) return true;
      const haystack = [
        a.login,
        a.id,
        ...a.unix_groups,
        ...a.server_ids.map(serverName),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(term);
    });
    const sorted = [...matched].sort((a, b) => {
      if (sort === "server") {
        const sa = a.server_ids.map(serverName).sort()[0] ?? "";
        const sb = b.server_ids.map(serverName).sort()[0] ?? "";
        return sa.localeCompare(sb);
      }
      if (sort === "rotated") {
        return (b.password_rotated_at ?? "").localeCompare(
          a.password_rotated_at ?? "",
        );
      }
      return a.login.localeCompare(b.login);
    });
    return sorted;
    // serverMap влияет на serverName (имена для сорта/поиска) — пересчитываем
    // при его обновлении.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allAccounts, search, sort, serverFilter, serverMap]);

  const selectedAccount = useMemo(
    () => (selectedId ? allAccounts.find((a) => a.id === selectedId) ?? null : null),
    [selectedId, allAccounts],
  );

  function selectId(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id);
    else next.delete("id");
    setParams(next, { replace: true });
  }

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_service / server users">
        <BlockedPane />
      </Shell>
    );
  }

  const loading = serversQ.loading || accountsQ.loading;
  const error = serversQ.error ?? accountsQ.error;
  const truncatedServers = accountsQ.data?.truncatedServers ?? 0;
  const failedServers = accountsQ.data?.failedServers ?? 0;

  const aside = (
    <aside className="border-r border-token surface flex flex-col min-h-0">
      <div className="border-b border-token px-3 py-2 shrink-0">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-dim" />
          <input
            className="bg-transparent outline-none flex-1 text-sm"
            placeholder={`Поиск по ${allAccounts.length} аккаунтам…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-dim flex-wrap">
          <span>Сервер:</span>
          <select
            className="surface-2 border border-token rounded px-2 py-0.5"
            value={serverFilter}
            onChange={(e) => setServerFilter(e.target.value)}
          >
            <option value="all">все серверы</option>
            {servers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name || s.hostname}
              </option>
            ))}
          </select>
          <span>Сорт:</span>
          <select
            className="surface-2 border border-token rounded px-2 py-0.5"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortMode)}
          >
            <option value="login">по login</option>
            <option value="server">по серверу</option>
            <option value="rotated">по ротации</option>
          </select>
        </div>
        {!loading && error == null && failedServers > 0 && (
          <div className="mt-2 alert-warn text-[11px]" role="status">
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
            <span>
              Аккаунты {failedServers} сервер(ов) не загрузились (нет доступа
              или сетевой сбой) — список может быть неполным.
            </span>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {loading && (
          <div className="px-3 py-6 text-xs text-dim text-center">Загрузка…</div>
        )}
        {!loading && error != null && (
          <div className="m-3 alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>{apiErrMsg(error, "Список пользователей не загрузился")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => {
                  serversQ.refetch();
                  accountsQ.refetch();
                }}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
        {!loading && error == null && filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-dim text-center">
            {allAccounts.length > 0
              ? "Под текущий фильтр аккаунтов нет."
              : "В отделе нет server_account'ов."}
          </div>
        )}
        {!loading && error == null && filtered.length > 0 && (
          <div className="px-2 flex flex-col gap-0.5">
            {filtered.map((a) => (
              <AccountRow
                key={a.id}
                account={a}
                active={selectedId === a.id}
                serverName={serverName}
                onSelect={() => selectId(a.id)}
              />
            ))}
          </div>
        )}
        {!loading && error == null && (
          <>
            <TruncationNotice
              shown={servers.length}
              total={serverTotal}
              className="mx-3 mt-2"
            />
            {truncatedServers > 0 && (
              <div className="text-[11px] text-dim mt-1 mx-3">
                На {truncatedServers} сервер(ах) аккаунтов больше лимита —
                откройте вкладку «Аккаунты» нужного сервера для полного списка.
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );

  return (
    <Shell breadcrumb="server_service / server users" middle={aside}>
      {selectedAccount ? (
        <AccountWorkzone
          account={selectedAccount}
          canReveal={canReveal}
          canOperate={canOperate}
          canManage={canManage}
          serverName={serverName}
          allServers={servers}
          onChanged={() => accountsQ.refetch()}
          onDeleted={() => {
            selectId(null);
            accountsQ.refetch();
          }}
        />
      ) : (
        <EmptyPane hasAny={allAccounts.length > 0} />
      )}
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function AccountRow({
  account,
  active,
  serverName,
  onSelect,
}: {
  account: ServerAccount;
  active: boolean;
  serverName: (id: string) => string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left ${active ? "active" : ""}`}
      title="Открыть карточку аккаунта"
    >
      <div className="flex items-center gap-2">
        <User className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate mono">{account.login}</div>
          <div className="text-[11px] text-dim flex items-center gap-2">
            <ServerIcon className="w-3 h-3 shrink-0" />
            <span className="truncate">
              {account.server_ids.map(serverName).join(", ") || "—"}
            </span>
          </div>
        </div>
        {account.has_sudo && (
          <span className="badge badge-warn flex items-center gap-1">
            <ShieldCheck className="w-3 h-3" /> sudo
          </span>
        )}
        <span className="badge">{account.source}</span>
      </div>
    </button>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Workzone — детальный просмотр + edit / rotate / delete (правая панель)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Рабочая зона одного аккаунта fleet-вида. Server-agnostic операции — правка
 * атрибутов (PATCH), ротация пароля в БД и hard-delete — плюс секция «Серверы
 * аккаунта»: per-server provision/update_on_host/deprovision/unbind и привязка
 * нового сервера. Финальные 403/404/409/503 приходят с backend'а; UI-гейты —
 * первичный визуальный слой.
 */
function AccountWorkzone({
  account,
  canReveal,
  canOperate,
  canManage,
  serverName,
  allServers,
  onChanged,
  onDeleted,
}: {
  account: ServerAccount;
  canReveal: boolean;
  canOperate: boolean;
  canManage: boolean;
  serverName: (id: string) => string;
  allServers: Server[];
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const { prompt, confirm } = useConfirm();
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const linkedUserLabel = useUserLabel(account.linked_user_id);
  const createdByLabel = useUserLabel(account.created_by);

  // Смена выбранного аккаунта — сбрасываем локальное состояние зоны (edit-режим
  // и предыдущую ошибку), чтобы не тащить их на другой аккаунт.
  useEffect(() => {
    setEditing(false);
    setErr(null);
  }, [account.id]);

  async function handleRotate() {
    if (pending || !canOperate) return;
    if (
      !(await confirm({
        title: "Ротация пароля",
        message: `Сгенерировать новый пароль для ${account.login} в БД? На серверы изменение не уезжает — для apply используйте worker-rotate на вкладке «Аккаунты» сервера.`,
        confirmLabel: "Ротировать",
      }))
    )
      return;
    setErr(null);
    setPending(true);
    try {
      const res = await accountsApi.rotateAccountUserInitiated(account.id);
      toast.success(
        `Пароль ${res.login} ротирован в БД (${formatMskShort(res.rotated_at)})`,
      );
      onChanged();
    } catch (e) {
      const msg = handleActionError(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setPending(false);
    }
  }

  async function handleDelete() {
    if (pending || !canManage) return;
    const { ok } = await prompt({
      title: "Удалить аккаунт",
      message: `Удалить аккаунт ${account.login}? Hard-delete во всех ${account.server_ids.length} серверах. OS-аккаунт на боксах не сносится — для этого Deprovision на вкладке сервера. Операция необратима.`,
      reason: true,
      reasonLabel: "Причина удаления",
      reasonRequired: true,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setErr(null);
    setPending(true);
    try {
      await accountsApi.deleteAccount(account.id);
      toast.success(`Аккаунт ${account.login} удалён`);
      onDeleted();
    } catch (e) {
      const msg = handleActionError(e);
      setErr(msg);
      toast.error(msg);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
      <div className="border-b border-token px-5 py-4 shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <User className="w-5 h-5 text-accent shrink-0" />
          <div className="flex-1 min-w-0">
            <h1 className="text-base font-semibold mono truncate">
              {account.login}
            </h1>
            <div className="text-[11px] text-dim truncate">
              {account.server_ids.map(serverName).join(", ") || "—"}
            </div>
          </div>
          {account.has_sudo && (
            <span className="badge badge-warn flex items-center gap-1">
              <ShieldCheck className="w-3 h-3" /> sudo
            </span>
          )}
          {!account.is_active && (
            <span className="badge badge-warn">inactive</span>
          )}
          <span className="badge">{account.source}</span>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-4 max-w-3xl">
        {err && <div className="alert-danger text-sm">{err}</div>}

        {editing ? (
          <AccountEditForm
            account={account}
            onCancel={() => setEditing(false)}
            onSaved={() => {
              setEditing(false);
              toast.success("Аккаунт обновлён");
              onChanged();
            }}
            onError={(e) => {
              const msg = handleActionError(e);
              setErr(msg);
              toast.error(msg);
            }}
          />
        ) : (
          <>
            <div>
              <div className="text-xs uppercase text-dim mb-2">Профиль</div>
              <StatRow
                k="account_id"
                v={<span className="mono">{account.id}</span>}
              />
              <StatRow
                k="login"
                v={<span className="mono">{account.login}</span>}
              />
              <StatRow
                k="серверы"
                v={
                  <span className="break-words">
                    {account.server_ids.map(serverName).join(", ") || "—"}
                  </span>
                }
              />
              <StatRow k="source" v={account.source} />
              <StatRow k="has_sudo" v={account.has_sudo ? "да" : "нет"} />
              <StatRow
                k="unix_groups"
                v={
                  account.unix_groups.length === 0 ? (
                    <span className="text-dim italic">—</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {account.unix_groups.map((g) => (
                        <span key={g} className="badge mono">
                          {g}
                        </span>
                      ))}
                    </div>
                  )
                }
              />
              <StatRow
                k="shell"
                v={
                  account.shell ? (
                    <span className="mono">{account.shell}</span>
                  ) : (
                    <span className="text-dim italic">—</span>
                  )
                }
              />
              <StatRow
                k="home_dir"
                v={
                  account.home_dir ? (
                    <span className="mono">{account.home_dir}</span>
                  ) : (
                    <span className="text-dim italic">—</span>
                  )
                }
              />
              <StatRow
                k="linked_user"
                v={
                  account.linked_user_id ? (
                    <span title={account.linked_user_id}>{linkedUserLabel}</span>
                  ) : (
                    <span className="text-dim italic">—</span>
                  )
                }
              />
              <StatRow
                k="is_active"
                v={account.is_active ? "активен" : "неактивен"}
              />
              <StatRow
                k="created_at"
                v={
                  <span className="mono">
                    {formatMskShort(account.created_at)}
                  </span>
                }
              />
              <StatRow
                k="updated_at"
                v={
                  <span className="mono">
                    {formatMskShort(account.updated_at)}
                  </span>
                }
              />
              <StatRow
                k="password_rotated_at"
                v={
                  <span className="mono">
                    {formatMskShort(account.password_rotated_at)}
                  </span>
                }
              />
              <StatRow
                k="created_by"
                v={
                  account.created_by ? (
                    <span title={account.created_by}>{createdByLabel}</span>
                  ) : (
                    <span className="text-dim italic">system</span>
                  )
                }
              />
            </div>

            <PasswordRevealCard
              key={account.id}
              account={account}
              canReveal={canReveal}
            />

            <div className="card">
              <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
                <KeyRound className="w-3 h-3" /> Ротация пароля
              </div>
              <div className="text-xs text-dim mb-3">
                Генерирует новый пароль в БД (apply на серверы — отдельно, через
                worker-rotate на вкладке сервера). Plaintext клиенту не
                возвращается.
              </div>
              <button
                className="btn flex items-center gap-1"
                disabled={pending || !canOperate}
                title={canOperate ? "Ротировать пароль в БД" : "Нет прав"}
                onClick={handleRotate}
                type="button"
              >
                <RotateCw className="w-4 h-4" /> Ротировать (БД)
              </button>
            </div>

            <ServersSection
              account={account}
              canOperate={canOperate}
              serverName={serverName}
              allServers={allServers}
              onChanged={onChanged}
            />

            <div className="flex items-center gap-2 border-t border-token pt-4">
              <button
                type="button"
                className="btn btn-danger flex items-center gap-1 mr-auto"
                disabled={pending || !canManage}
                title={canManage ? undefined : "Нет прав на удаление"}
                onClick={handleDelete}
              >
                <Trash2 className="w-4 h-4" /> Удалить
              </button>
              <button
                type="button"
                className="btn btn-primary flex items-center gap-1"
                disabled={pending || !canManage}
                title={canManage ? undefined : "Нет прав на редактирование"}
                onClick={() => setEditing(true)}
              >
                <Edit3 className="w-4 h-4" /> Редактировать
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

/**
 * Дружелюбное сообщение по ошибке мутации. 403 — нет прав в server-зоне;
 * остальное — обычный envelope.
 */
function handleActionError(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) {
    return "Недостаточно прав для этой операции.";
  }
  if (e instanceof ApiError && e.status === 404) {
    return "Аккаунт не найден (возможно, уже удалён).";
  }
  return apiErrMsg(e, "Операция не удалась");
}

/**
 * Дружелюбное сообщение по ошибке per-server dispatch'а. Сверх общих 403/404
 * раскрывает 409 (конфликт — login занят / нет пароля у discovered / нечего
 * отвязывать) и 503 (worker недоступен).
 */
function handleDispatchError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав для этой операции.";
    if (e.status === 404) return "Аккаунт или сервер не найдены.";
    if (e.status === 409) {
      return apiErrMsg(
        e,
        "Конфликт: login занят, нет пароля у discovered-аккаунта или нечего отвязывать.",
      );
    }
    if (e.status === 503) {
      return "Worker недоступен — задача не поставлена. Повторите позже.";
    }
  }
  return apiErrMsg(e, "Операция не удалась");
}

/**
 * Presence-бэйдж сервера в секции «Серверы аккаунта». В fleet-вьюхе нет
 * отдельного флага «present_on_host», поэтому presence выводим из тех же
 * признаков, что и `provisionBadge` на вкладке сервера: привязка
 * (`server_ids`) + managed/discovered-without-password.
 */
function presenceBadge(
  a: ServerAccount,
  serverId: string,
): { label: string; kind: "ok" | "warn" | "danger" | "" } {
  const linked = a.server_ids.includes(serverId);
  if (!linked) return { label: "не привязан", kind: "danger" };
  if (a.source === "discovered" && !a.password_rotated_at) {
    return { label: "discovered", kind: "warn" };
  }
  return { label: "привязан", kind: "ok" };
}

// ───────────────────────────────────────────────────────────────────────────
// Серверы аккаунта — per-server provision/update/deprovision/unbind + bind
// ───────────────────────────────────────────────────────────────────────────

/**
 * Секция управления присутствием аккаунта на конкретных боксах. Edit-форма выше
 * меняет атрибуты в БД с авто-fan-out'ом `update_on_host`; здесь — точечный
 * lifecycle OS-юзера на каждом сервере (`useradd`/`usermod`/`userdel`), отвязка
 * связки и привязка нового сервера отдела.
 */
function ServersSection({
  account,
  canOperate,
  serverName,
  allServers,
  onChanged,
}: {
  account: ServerAccount;
  canOperate: boolean;
  serverName: (id: string) => string;
  allServers: Server[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  // Pending держим по конкретному серверу — чтобы не гасить кнопки всех строк
  // на время одной операции.
  const [busyServer, setBusyServer] = useState<string | null>(null);
  const [bindTarget, setBindTarget] = useState("");
  const [binding, setBinding] = useState(false);

  // Discovered-аккаунт без сохранённого пароля backend отбивает на provision
  // (ACCOUNT_HAS_NO_PASSWORD) — provision уйдёт только с force_password.
  const discoveredNoPassword =
    account.source === "discovered" && !account.password_rotated_at;

  // Серверы отдела, к которым аккаунт ещё не привязан — кандидаты на bind.
  const bindCandidates = useMemo(
    () => allServers.filter((s) => !account.server_ids.includes(s.id)),
    [allServers, account.server_ids],
  );

  async function runOn(
    serverId: string,
    fn: () => Promise<unknown>,
    ok: string,
  ) {
    if (busyServer) return;
    setBusyServer(serverId);
    try {
      await fn();
      toast.success(ok);
      onChanged();
    } catch (e) {
      toast.error(handleDispatchError(e));
    } finally {
      setBusyServer(null);
    }
  }

  async function handleProvision(serverId: string) {
    if (!canOperate) return;
    let force = false;
    if (discoveredNoPassword) {
      // Без пароля useradd не уедет — спрашиваем подтверждение на генерацию.
      if (
        !(await confirm({
          title: "Provision discovered-аккаунта",
          message: `У ${account.login} нет сохранённого пароля (discovered). Сгенерировать новый и применить на боксе через chpasswd (force_password)?`,
          confirmLabel: "Provision + force_password",
        }))
      )
        return;
      force = true;
    }
    void runOn(
      serverId,
      () =>
        accountsApi.provisionOnHost(serverId, account.id, {
          force_password: force,
        }),
      `Provision-task поставлен в очередь (${serverName(serverId)})`,
    );
  }

  async function handleDeprovision(serverId: string) {
    if (!canOperate) return;
    const { ok, removeHome } = await confirmDeprovision(serverId);
    if (!ok) return;
    void runOn(
      serverId,
      () =>
        accountsApi.deprovisionOnHost(serverId, account.id, {
          remove_home: removeHome,
        }),
      `Deprovision-task поставлен в очередь (${serverName(serverId)})`,
    );
  }

  // remove_home выбирается без отдельного чекбокса: ConfirmDialog не несёт
  // boolean-опции, поэтому разводим на два варианта подтверждения.
  async function confirmDeprovision(
    serverId: string,
  ): Promise<{ ok: boolean; removeHome: boolean }> {
    const removeHome = await confirm({
      title: "Deprovision",
      message: `Удалить OS-пользователя ${account.login} с сервера ${serverName(serverId)}?\n\nУдалить и home-каталог (userdel --remove)? «Отмена» снесёт только учётку, дальше будет ещё одно подтверждение.`,
      confirmLabel: "Удалить вместе с home",
      cancelLabel: "Только учётку",
      danger: true,
    });
    if (removeHome) return { ok: true, removeHome: true };
    // Пользователь выбрал «только учётку» — подтверждаем сам факт удаления.
    const justUser = await confirm({
      title: "Deprovision",
      message: `Удалить OS-пользователя ${account.login} с сервера ${serverName(serverId)} без удаления home?`,
      confirmLabel: "Deprovision",
      danger: true,
    });
    return { ok: justUser, removeHome: false };
  }

  async function handleUnbind(serverId: string) {
    if (!canOperate) return;
    if (account.server_ids.length <= 1) {
      toast.error("Нельзя отвязать последний сервер аккаунта.");
      return;
    }
    if (
      !(await confirm({
        title: "Отвязать сервер",
        message: `Отвязать аккаунт ${account.login} от ${serverName(serverId)}? Связка снимется, OS-юзер на боксе останется (для удаления — Deprovision).`,
        confirmLabel: "Отвязать",
        danger: true,
      }))
    )
      return;
    void runOn(
      serverId,
      () => accountsApi.unbindAccountServer(account.id, serverId),
      `Аккаунт отвязан от ${serverName(serverId)}`,
    );
  }

  async function handleBind() {
    if (!canOperate || !bindTarget || binding) return;
    setBinding(true);
    try {
      await accountsApi.bindAccountServers(account.id, {
        server_ids: [bindTarget],
      });
      toast.success(`Аккаунт привязан к ${serverName(bindTarget)}`);
      setBindTarget("");
      onChanged();
    } catch (e) {
      toast.error(handleDispatchError(e));
    } finally {
      setBinding(false);
    }
  }

  return (
    <div className="card">
      <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
        <ServerIcon className="w-3 h-3" /> Серверы аккаунта
      </div>
      <div className="text-xs text-dim mb-3">
        Per-server lifecycle OS-юзера: Provision (`useradd`), Update on host
        (`usermod`), Deprovision (`userdel`), Unbind (снять связку без удаления
        на боксе). Атрибуты (sudo/группы/shell) меняются через «Редактировать» с
        авто-рассылкой на привязанные серверы.
      </div>

      {account.server_ids.length === 0 ? (
        <div className="text-xs text-dim italic mb-3">
          Аккаунт ни к одному серверу не привязан.
        </div>
      ) : (
        <div className="flex flex-col gap-2 mb-3">
          {account.server_ids.map((sid) => {
            const presence = presenceBadge(account, sid);
            const busy = busyServer === sid;
            const disabled = busy || !!busyServer || !canOperate;
            const noPrivReason = canOperate ? undefined : "Нет прав";
            return (
              <div
                key={sid}
                className="border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap"
              >
                <ServerIcon className="w-4 h-4 text-dim shrink-0" />
                <span className="text-sm mono flex-1 min-w-[140px] truncate">
                  {serverName(sid)}
                </span>
                <span
                  className={`badge${presence.kind ? ` badge-${presence.kind}` : ""}`}
                >
                  {presence.label}
                </span>
                <button
                  className="btn btn-sm flex items-center gap-1"
                  disabled={disabled}
                  title={
                    noPrivReason ??
                    (discoveredNoPassword
                      ? "useradd (запросит force_password)"
                      : "useradd на боксе")
                  }
                  onClick={() => handleProvision(sid)}
                  type="button"
                >
                  <Power className="w-3.5 h-3.5" /> Provision
                </button>
                <button
                  className="btn btn-sm flex items-center gap-1"
                  disabled={disabled}
                  title={noPrivReason ?? "usermod синхронизирует атрибуты"}
                  onClick={() =>
                    runOn(
                      sid,
                      () => accountsApi.updateOnHost(sid, account.id),
                      `Update-on-host-task поставлен в очередь (${serverName(sid)})`,
                    )
                  }
                  type="button"
                >
                  <RotateCw className="w-3.5 h-3.5" /> Update on host
                </button>
                <button
                  className="btn btn-sm btn-danger flex items-center gap-1"
                  disabled={disabled}
                  title={noPrivReason ?? "userdel на боксе"}
                  onClick={() => handleDeprovision(sid)}
                  type="button"
                >
                  <Trash2 className="w-3.5 h-3.5" /> Deprovision
                </button>
                <button
                  className="btn btn-sm btn-danger flex items-center gap-1"
                  disabled={disabled || account.server_ids.length <= 1}
                  title={
                    noPrivReason ??
                    (account.server_ids.length <= 1
                      ? "Нельзя отвязать последний сервер"
                      : "Снять связку (OS-юзер остаётся)")
                  }
                  onClick={() => handleUnbind(sid)}
                  type="button"
                >
                  <Unlink className="w-3.5 h-3.5" /> Unbind
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="border-t border-token pt-3 flex items-center gap-2 flex-wrap">
        <span className="text-xs text-dim">Привязать сервер:</span>
        <select
          className="surface-2 border border-token rounded px-2 py-1 text-sm flex-1 min-w-[160px]"
          value={bindTarget}
          disabled={!canOperate || binding || bindCandidates.length === 0}
          onChange={(e) => setBindTarget(e.target.value)}
        >
          <option value="">
            {bindCandidates.length === 0
              ? "— нет доступных серверов —"
              : "— выберите сервер —"}
          </option>
          {bindCandidates.map((s) => (
            <option key={s.id} value={s.id}>
              {s.display_name || s.hostname}
            </option>
          ))}
        </select>
        <button
          className="btn btn-sm btn-primary flex items-center gap-1"
          disabled={!canOperate || binding || !bindTarget}
          title={canOperate ? "Привязать аккаунт к серверу" : "Нет прав"}
          onClick={handleBind}
          type="button"
        >
          <Link2 className="w-3.5 h-3.5" /> {binding ? "Привязываем…" : "Привязать"}
        </button>
      </div>
    </div>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 py-1 text-sm">
      <span className="text-xs text-dim w-44 shrink-0">{k}</span>
      <span className="flex-1 min-w-0 break-words">{v}</span>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Edit form (PATCH has_sudo / unix_groups / shell)
// ───────────────────────────────────────────────────────────────────────────

function AccountEditForm({
  account,
  onCancel,
  onSaved,
  onError,
}: {
  account: ServerAccount;
  onCancel: () => void;
  onSaved: () => void;
  onError: (e: unknown) => void;
}) {
  const [hasSudo, setHasSudo] = useState(account.has_sudo);
  const [groups, setGroups] = useState(account.unix_groups.join(", "));
  const [shell, setShell] = useState(account.shell ?? "");
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    const unixGroups = groups
      .split(",")
      .map((g) => g.trim())
      .filter(Boolean);
    const groupsErr = validateUnixGroups(unixGroups);
    if (groupsErr) {
      setErr(groupsErr);
      return;
    }
    setErr(null);
    setPending(true);
    try {
      const body: ServerAccountUpdateRequest = {
        has_sudo: hasSudo,
        unix_groups: unixGroups,
        shell: shell.trim() || null,
      };
      await accountsApi.updateAccount(account.id, body);
      onSaved();
    } catch (e) {
      setErr(handleActionError(e));
      onError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <div className="text-xs uppercase text-dim flex items-center gap-2">
        <Edit3 className="w-3 h-3" /> Редактирование атрибутов
      </div>
      <div className="text-xs text-dim">
        При изменении backend сам разошлёт `update_on_host` на привязанные
        серверы. Пароль здесь не меняется (для этого — ротация).
      </div>
      {err && <div className="alert-danger text-sm">{err}</div>}

      <FormRow label="sudo">
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={hasSudo}
            onChange={(e) => setHasSudo(e.target.checked)}
          />
          <span>has_sudo</span>
        </label>
      </FormRow>
      <FormRow label="unix_groups" hint="csv: docker, wheel">
        <input
          className="input mono"
          value={groups}
          onChange={(e) => setGroups(e.target.value)}
          placeholder="docker, wheel"
        />
      </FormRow>
      <FormRow label="shell">
        <input
          className="input mono"
          value={shell}
          onChange={(e) => setShell(e.target.value)}
          placeholder="/bin/bash"
        />
      </FormRow>
      <div className="mt-2 flex gap-2 justify-end">
        <button type="button" className="btn" onClick={onCancel}>
          Отмена
        </button>
        <button type="submit" className="btn btn-primary" disabled={pending}>
          {pending ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>
    </form>
  );
}

function FormRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-dim text-xs">
        {label}
        {hint && <span className="ml-2 italic">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Password reveal card (в правой рабочей зоне)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Reveal-блок пароля в рабочей зоне. По клику «показать» дёргает карточку
 * аккаунта (`getAccount`), декодит `password_b64` → plaintext. `null` →
 * понятная причина (нет view_password либо нет сохранённого пароля), 429 гасит
 * кнопку на retry-окно, 403 — отдельная причина. Сбрасывается при смене
 * аккаунта (key по account.id монтирует блок заново).
 */
function PasswordRevealCard({
  account,
  canReveal,
}: {
  account: ServerAccount;
  canReveal: boolean;
}) {
  const toast = useToast();
  const [plain, setPlain] = useState<string | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [throttleUntil, setThrottleUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (throttleUntil <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [throttleUntil]);

  const throttleLeft = Math.max(0, Math.ceil((throttleUntil - now) / 1000));
  const shown = plain !== null;

  // Discovered-аккаунт без ротации обычно не имеет ciphertext'а в БД — backend
  // вернёт null даже держателю view_password. Подсказка до запроса.
  const likelyNoPassword =
    account.source === "discovered" && !account.password_rotated_at;

  async function handleReveal() {
    if (revealing || throttleLeft > 0) return;
    setRevealing(true);
    setReason(null);
    try {
      const fresh = await accountsApi.getAccount(account.id);
      if (fresh.password_b64 === null) {
        setReason(
          likelyNoPassword
            ? "У аккаунта нет сохранённого пароля (discovered, без ротации)."
            : "Пароль скрыт: нет права view_password или пароль отсутствует.",
        );
        return;
      }
      try {
        setPlain(fromBase64(fresh.password_b64));
      } catch {
        setPlain(fresh.password_b64);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        const secs = e.retryAfter ?? 60;
        setThrottleUntil(Date.now() + secs * 1000);
        setNow(Date.now());
        toast.error(`Reveal-лимит: повторите через ${secs} сек`);
      } else if (e instanceof ApiError && e.status === 403) {
        setReason("Недостаточно прав: нужен view_password.");
      } else {
        toast.error(apiErrMsg(e, "Не удалось получить пароль"));
      }
    } finally {
      setRevealing(false);
    }
  }

  async function handleCopy() {
    if (plain === null || typeof navigator === "undefined" || !navigator.clipboard)
      return;
    try {
      await navigator.clipboard.writeText(plain);
      toast.success("Пароль скопирован");
    } catch {
      toast.error("Буфер обмена недоступен");
    }
  }

  return (
    <div className="card">
      <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
        <KeyRound className="w-3 h-3" /> Пароль
      </div>
      <div className="text-xs text-dim mb-3">
        Хранится зашифрованным (AES-256-GCM). Показ требует права view_password,
        пишет CRITICAL audit и режется reveal-rate-limit'ом.
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <div
          className={`mono text-sm flex-1 min-w-[200px] break-all ${shown ? "" : "text-dim"}`}
        >
          {shown ? plain : "••••••••••••"}
        </div>
        {shown ? (
          <>
            <button
              className="btn btn-sm flex items-center gap-1"
              onClick={handleCopy}
              type="button"
            >
              <Copy className="w-4 h-4" /> Копировать
            </button>
            <button
              className="btn btn-sm flex items-center gap-1"
              onClick={() => setPlain(null)}
              type="button"
            >
              <EyeOff className="w-4 h-4" /> Скрыть
            </button>
          </>
        ) : (
          <button
            className="btn btn-sm flex items-center gap-1"
            onClick={handleReveal}
            disabled={!canReveal || revealing || throttleLeft > 0}
            title={
              !canReveal
                ? "Нужна роль server.operator+ (и грант view_password)"
                : "Раскрыть пароль (CRITICAL audit)"
            }
            type="button"
          >
            <Eye className="w-4 h-4" />
            {revealing
              ? "Запрашиваем…"
              : throttleLeft > 0
                ? `Подождите ${throttleLeft}с`
                : "Показать"}
          </button>
        )}
      </div>
      {reason && <div className="text-xs text-dim mt-3">{reason}</div>}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function EmptyPane({ hasAny }: { hasAny: boolean }) {
  return (
    <section className="flex-1 min-w-0 overflow-hidden flex items-center justify-center">
      <div className="empty-card max-w-md text-center">
        <Users className="w-10 h-10 mx-auto text-dim mb-3" />
        <div className="text-sm text-dim">
          {hasAny
            ? "Выберите аккаунт слева для просмотра и управления."
            : "В отделе нет server_account'ов. Аккаунты заводятся на вкладке «Аккаунты» карточки сервера."}
        </div>
      </div>
    </section>
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
          доступа к аккаунтам серверов — работайте под департаментной ролью
          (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}
