/**
 * Страница /server/users — сводный список всех server_account'ов отдела.
 *
 * Per-server аккаунты живут на вкладке «Аккаунты» карточки сервера; здесь —
 * единый список «все пользователи разом» по всем доступным серверам. Backend
 * не отдаёт общую выдачу аккаунтов (`GET /server-accounts` требует
 * обязательный `server_id`), поэтому страница сначала тянет список серверов
 * (`listServers`), затем веером дёргает `listAccounts` на каждый и сшивает
 * результат: один аккаунт может быть привязан к нескольким серверам, дубли по
 * `id` объединяются.
 *
 * Список показывает login, серверы (по имени), sudo/группы и reveal пароля.
 * Клик по строке открывает модалку с полной карточкой аккаунта и управлением:
 * детальный просмотр, edit (PATCH has_sudo/unix_groups/shell), ротация пароля
 * (только БД) и удаление. Per-server provision/deprovision сюда не выносим —
 * это привязано к конкретному серверу и живёт на вкладке «Аккаунты» карточки.
 *
 * account_admin / logging_* отрезаны от server-зоны backend'ом
 * (`PLATFORM_ADMIN_BUSINESS_DATA_DENIED`) — для них BlockedPane вместо мёртвой
 * страницы. dep_admin видит аккаунты серверов своего отдела; server.*-роли —
 * по матрице.
 */
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
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
import type { ServerAccount, ServerAccountUpdateRequest } from "@/api/server/types";

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
  const [openId, setOpenId] = useState<string | null>(null);

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

  const openAccount = useMemo(
    () => (openId ? allAccounts.find((a) => a.id === openId) ?? null : null),
    [openId, allAccounts],
  );

  if (zoneBlocked) {
    return (
      <Shell breadcrumb="server_service / users">
        <BlockedPane />
      </Shell>
    );
  }

  const loading = serversQ.loading || accountsQ.loading;
  const error = serversQ.error ?? accountsQ.error;
  const truncatedServers = accountsQ.data?.truncatedServers ?? 0;
  const failedServers = accountsQ.data?.failedServers ?? 0;

  return (
    <Shell breadcrumb="server_service / users">
      <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="border-b border-token px-5 py-4 shrink-0">
          <div className="flex items-center gap-3 flex-wrap">
            <Users className="w-6 h-6 text-accent shrink-0" />
            <div className="flex-1 min-w-0">
              <h1 className="text-lg font-semibold">Пользователи серверов</h1>
              <div className="text-xs text-dim">
                Все server_account'ы отдела по всем серверам. Клик по строке —
                карточка с управлением; per-server provision — на вкладке
                «Аккаунты» карточки сервера.
              </div>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-2 surface-2 border border-token rounded px-2 py-1 flex-1 min-w-[16rem]">
              <Search className="w-4 h-4 text-dim shrink-0" />
              <input
                className="bg-transparent outline-none flex-1 text-sm"
                placeholder={`Поиск по ${allAccounts.length} аккаунтам…`}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <label className="text-xs text-dim flex items-center gap-1.5">
              Сервер:
              <select
                className="surface-2 border border-token rounded px-2 py-1"
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
            </label>
            <label className="text-xs text-dim flex items-center gap-1.5">
              Сорт:
              <select
                className="surface-2 border border-token rounded px-2 py-1"
                value={sort}
                onChange={(e) => setSort(e.target.value as SortMode)}
              >
                <option value="login">по login</option>
                <option value="server">по серверу</option>
                <option value="rotated">по ротации</option>
              </select>
            </label>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-5">
          {loading && (
            <div className="py-10 text-sm text-dim text-center">Загрузка…</div>
          )}

          {!loading && error != null && (
            <div className="alert alert-danger flex items-start gap-2 max-w-xl">
              <AlertCircle className="w-5 h-5 mt-0.5 shrink-0" />
              <div className="flex-1 text-sm">
                <div>
                  {apiErrMsg(error, "Список пользователей не загрузился")}
                </div>
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

          {!loading && error == null && failedServers > 0 && (
            <div className="alert alert-warn text-xs max-w-xl mb-3">
              Аккаунты {failedServers} сервер(ов) не загрузились (нет доступа
              или сетевой сбой) — список может быть неполным.
            </div>
          )}

          {!loading && error == null && filtered.length === 0 && (
            <EmptyPane hasAny={allAccounts.length > 0} />
          )}

          {!loading && error == null && filtered.length > 0 && (
            <div className="flex flex-col gap-1.5 max-w-7xl">
              {filtered.map((a) => (
                <AccountRow
                  key={a.id}
                  account={a}
                  canReveal={canReveal}
                  serverName={serverName}
                  onOpen={() => setOpenId(a.id)}
                />
              ))}
            </div>
          )}

          {!loading && error == null && (
            <>
              <TruncationNotice
                shown={servers.length}
                total={serverTotal}
                className="mt-3 max-w-7xl"
              />
              {truncatedServers > 0 && (
                <div className="text-[11px] text-dim mt-1 max-w-7xl">
                  На {truncatedServers} сервер(ах) аккаунтов больше лимита —
                  откройте вкладку «Аккаунты» нужного сервера для полного
                  списка.
                </div>
              )}
            </>
          )}
        </div>
      </section>

      {openAccount && (
        <AccountManageModal
          account={openAccount}
          canReveal={canReveal}
          canOperate={canOperate}
          canManage={canManage}
          serverName={serverName}
          onClose={() => setOpenId(null)}
          onChanged={() => accountsQ.refetch()}
          onDeleted={() => {
            setOpenId(null);
            accountsQ.refetch();
          }}
        />
      )}
    </Shell>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function AccountRow({
  account,
  canReveal,
  serverName,
  onOpen,
}: {
  account: ServerAccount;
  canReveal: boolean;
  serverName: (id: string) => string;
  onOpen: () => void;
}) {
  return (
    <div className="cred-row text-left flex-col items-stretch gap-2">
      <button
        type="button"
        onClick={onOpen}
        className="flex items-center gap-3 text-left w-full"
        title="Открыть карточку аккаунта"
      >
        <User className="w-4 h-4 text-dim shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate mono">{account.login}</div>
          <div className="text-[11px] text-dim flex items-center gap-2 flex-wrap">
            <ServerIcon className="w-3 h-3 shrink-0" />
            <span className="truncate">
              {account.server_ids.map(serverName).join(", ") || "—"}
            </span>
          </div>
        </div>
        <div className="hidden sm:flex items-center gap-1.5 shrink-0">
          {account.has_sudo && (
            <span className="badge badge-warn flex items-center gap-1">
              <ShieldCheck className="w-3 h-3" /> sudo
            </span>
          )}
          {account.unix_groups.slice(0, 3).map((g) => (
            <span key={g} className="badge mono">
              {g}
            </span>
          ))}
          {account.unix_groups.length > 3 && (
            <span className="badge">+{account.unix_groups.length - 3}</span>
          )}
          <span className="badge">{account.source}</span>
        </div>
        <div className="hidden md:flex flex-col items-end text-[11px] text-dim shrink-0">
          <span>rotated</span>
          <span>{formatMskShort(account.password_rotated_at)}</span>
        </div>
      </button>
      <PasswordRevealRow account={account} canReveal={canReveal} />
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Manage modal — детальный просмотр + edit / rotate / delete
// ───────────────────────────────────────────────────────────────────────────

/**
 * Модалка управления одним аккаунтом fleet-вида. Provision/deprovision на
 * конкретный бокс сюда не входит (нет single-server контекста) — только
 * server-agnostic операции: правка атрибутов (PATCH), ротация пароля в БД и
 * hard-delete. Финальные 403 приходят с backend'а; UI-гейты — первичный
 * визуальный слой.
 */
function AccountManageModal({
  account,
  canReveal,
  canOperate,
  canManage,
  serverName,
  onClose,
  onChanged,
  onDeleted,
}: {
  account: ServerAccount;
  canReveal: boolean;
  canOperate: boolean;
  canManage: boolean;
  serverName: (id: string) => string;
  onClose: () => void;
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
    <Dialog.Root open onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          style={{ maxWidth: "44rem", width: "92vw" }}
          aria-describedby={undefined}
        >
          <div className="modal-header">
            <User className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold mono flex-1">
              {account.login}
            </Dialog.Title>
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

          <div className="modal-body flex flex-col gap-4">
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
                        <span title={account.linked_user_id}>
                          {linkedUserLabel}
                        </span>
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

                <PasswordRevealCard account={account} canReveal={canReveal} />

                <div className="card">
                  <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
                    <KeyRound className="w-3 h-3" /> Ротация пароля
                  </div>
                  <div className="text-xs text-dim mb-3">
                    Генерирует новый пароль в БД (apply на серверы — отдельно,
                    через worker-rotate на вкладке сервера). Plaintext клиенту
                    не возвращается.
                  </div>
                  <button
                    className="btn flex items-center gap-1"
                    disabled={pending || !canOperate}
                    title={
                      canOperate ? "Ротировать пароль в БД" : "Нет прав"
                    }
                    onClick={handleRotate}
                    type="button"
                  >
                    <RotateCw className="w-4 h-4" /> Ротировать (БД)
                  </button>
                </div>
              </>
            )}
          </div>

          {!editing && (
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-danger flex items-center gap-1 mr-auto"
                disabled={pending || !canManage}
                title={canManage ? undefined : "Нет прав на удаление"}
                onClick={handleDelete}
              >
                <Trash2 className="w-4 h-4" /> Удалить
              </button>
              <button type="button" className="btn" onClick={onClose}>
                Закрыть
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
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
// Password reveal (inline row in list)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Inline-reveal пароля в строке списка. По клику «показать» дёргает карточку
 * аккаунта (`getAccount`), декодит `password_b64` → plaintext. `null` →
 * понятная причина (нет view_password либо нет сохранённого пароля), 429 гасит
 * кнопку на retry-окно, 403 — отдельная причина.
 */
function PasswordRevealRow({
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
            ? "Нет сохранённого пароля (discovered, без ротации)."
            : "Скрыт: нет права view_password или пароль отсутствует.",
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
    <div className="flex items-center gap-2 flex-wrap border-t border-token pt-2">
      <KeyRound className="w-3.5 h-3.5 text-dim shrink-0" />
      <div
        className={`mono text-xs flex-1 min-w-[160px] break-all ${shown ? "" : "text-dim"}`}
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
            <Copy className="w-3.5 h-3.5" /> Копировать
          </button>
          <button
            className="btn btn-sm flex items-center gap-1"
            onClick={() => setPlain(null)}
            type="button"
          >
            <EyeOff className="w-3.5 h-3.5" /> Скрыть
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
          <Eye className="w-3.5 h-3.5" />
          {revealing
            ? "Запрашиваем…"
            : throttleLeft > 0
              ? `Подождите ${throttleLeft}с`
              : "Показать"}
        </button>
      )}
      {reason && (
        <span className="text-[11px] text-dim w-full sm:w-auto">{reason}</span>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Password reveal card (внутри модалки)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Reveal-блок пароля в модалке. Тот же поток, что в строке списка, но в
 * card-обёртке. Сбрасывается при смене аккаунта (модалка монтируется заново
 * на каждый openId).
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
    <div className="empty-card max-w-md text-center mx-auto mt-6">
      <Users className="w-10 h-10 mx-auto text-dim mb-3" />
      <div className="text-sm text-dim">
        {hasAny
          ? "Под текущий фильтр аккаунтов нет."
          : "В отделе нет server_account'ов. Аккаунты заводятся на вкладке «Аккаунты» карточки сервера."}
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
          доступа к аккаунтам серверов — работайте под департаментной ролью
          (dep_admin или server.*).
        </div>
      </div>
    </section>
  );
}
