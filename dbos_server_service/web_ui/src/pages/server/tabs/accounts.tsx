/**
 * Accounts-вкладка карточки сервера.
 *
 * Слева — список `server_account`'ов, привязанных к этому серверу
 * (`listAccounts({server_id})`), справа — side-panel с деталями выбранного:
 *  - metadata (login / scope / source / sudo / groups / timestamps);
 *  - actions: edit, delete, unbind, rotate (sync / worker), provision /
 *    update_on_host / deprovision на текущем сервере;
 *  - + Создать аккаунт — форма `createAccount({server_ids:[serverId]})`.
 *
 * Backend источник: `server_service/src/api/v1/endpoints/server_accounts.py`
 * и worker-dispatch секции `/server-accounts/{id}/{provision|update_on_host|
 * deprovision|rotate|rotate_password|servers}`.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  AlertTriangle,
  Copy,
  Edit3,
  Eye,
  EyeOff,
  KeyRound,
  Link2,
  Plus,
  Power,
  RefreshCw,
  RotateCw,
  Trash2,
  Unlink,
  User,
  Users,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import { fromBase64 } from "@/lib/base64";
import { formatMskShort } from "@/lib/datetime";
import { useUserLabel } from "@/lib/labels";
import * as accountsApi from "@/api/server/accounts";
import type {
  Server,
  ServerAccount,
  ServerAccountUpdateRequest,
} from "@/api/server/types";
import { listVmAccounts, type Vm, type VmAccount } from "@/api/server/vms";
import { mockVmAccounts } from "@/mocks/vm";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { RotateDispatchResult } from "@/pages/server/_rotateResult";
import {
  PASSWORD_POLICY_HINT,
  validateAccountPassword,
  accountPasswordPolicyError,
} from "@/pages/server/_serverShared";
import { LinkAccountModal } from "./_linkAccountModal";
import type { EntityRef } from "./_entity";

interface Props {
  serverId: string;
  server?: Server;
  /**
   * Сущность вкладки. Для `kind:"vm"` рендерится read-only список учёток ВМ;
   * без `entity` (или `kind:"server"`) работает прежний серверный путь по
   * `serverId`/`server`.
   */
  entity?: EntityRef;
}

const fmtTs = formatMskShort;

// Зеркало `server_service/src/schemas/server_account.py`:
//   login — `^[A-Za-z0-9._\-]+$`, 1..128 символов.
//   unix_groups — POSIX group name `^[a-z_][a-z0-9_-]{0,31}$`.
const LOGIN_RE = /^[A-Za-z0-9._-]+$/;
const POSIX_GROUP_RE = /^[a-z_][a-z0-9_-]{0,31}$/;

function validateLogin(value: string): string | null {
  if (value.length > 128) return "login: максимум 128 символов";
  if (!LOGIN_RE.test(value)) {
    return "login: допустимы латиница, цифры и символы . _ -";
  }
  return null;
}

function validateUnixGroups(groups: string[]): string | null {
  for (const g of groups) {
    if (!POSIX_GROUP_RE.test(g)) {
      return `unix_groups: '${g}' не POSIX-имя (строчные, цифры, _ -, до 32 симв.)`;
    }
  }
  return null;
}

/** Тонкая полоска scope/source — для row и detail. */
type Scope = "personal" | "shared" | "service";

/**
 * Backend не отдаёт явный scope в `ServerAccount`: модель построена вокруг
 * `linked_user_id` (есть → персональный аккаунт), `source` и `has_sudo`.
 * UI-уровень сводит эти признаки к простому badge.
 */
function deriveScope(a: ServerAccount): Scope {
  if (a.linked_user_id) return "personal";
  if (a.source === "discovered") return "shared";
  return "service";
}

function scopeBadgeKind(scope: Scope): "ok" | "warn" | "" {
  if (scope === "personal") return "ok";
  if (scope === "service") return "warn";
  return "";
}

/**
 * Provision-state badge — производный признак: есть ли строка connector'а на
 * текущем сервере (учётка в `server_ids` => provisioned), плюс пометка
 * managed/discovered. Discovered без пароля помечается отдельно — для UX
 * (без него provision не уедет без `force_password`).
 */
function provisionBadge(
  a: ServerAccount,
  serverId: string,
): { label: string; kind: "ok" | "warn" | "danger" | "" } {
  const linked = a.server_ids.includes(serverId);
  if (!linked) return { label: "unlinked", kind: "danger" };
  if (a.source === "discovered" && !a.password_rotated_at) {
    return { label: "discovered", kind: "warn" };
  }
  return { label: "linked", kind: "ok" };
}

export function AccountsTab({ serverId, server, entity }: Props) {
  if (entity?.kind === "vm") {
    return <VmAccountsList vm={entity.vm} mock={entity.mock} />;
  }
  return <ServerAccountsTab serverId={serverId} server={server} />;
}

function ServerAccountsTab({ serverId, server }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [linking, setLinking] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  // На RBAC уровне UI:
  //  - server.operator / server.admin / dep_admin → provision, rotate, unbind,
  //    deprovision;
  //  - server.admin / dep_admin → create/edit/delete учётки.
  // account_admin / logging_admin сюда не доходят — server_service отрезает их
  // 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED, страница /server для них закрыта.
  // Финальные 403 всё равно приходят с backend'а — это первичный визуальный
  // gate, без двойной проверки прав.
  const isDepAdmin = persona.platform_role === "dep_admin";
  const serverRole = persona.service_roles.server;
  const canOperate =
    isDepAdmin || serverRole === "admin" || serverRole === "operator";
  const canManage = isDepAdmin || serverRole === "admin";

  const listQ = useQuery(
    () => accountsApi.listAccounts({ server_id: serverId, limit: 200 }),
    [serverId, refreshTick],
  );

  const items: ServerAccount[] = useMemo(() => {
    if (!listQ.data) return [];
    return listQ.data.items;
  }, [listQ.data]);

  // Серверная истина по количеству привязанных аккаунтов: страница тянется с
  // капом limit:200, поэтому счётчик и баннер опираются на `total`, а не на
  // длину усечённого массива.
  const total = useMemo(() => {
    const data = listQ.data;
    if (data && "total" in data) return data.total;
    return items.length;
  }, [listQ.data, items.length]);

  const selected = useMemo(
    () => (selectedId ? items.find((a) => a.id === selectedId) ?? null : null),
    [items, selectedId],
  );

  const refresh = useCallback(() => setRefreshTick((t) => t + 1), []);

  function handleSelect(id: string) {
    setCreating(false);
    setSelectedId(id);
  }

  function handleStartCreate() {
    setSelectedId(null);
    setCreating(true);
  }

  return (
    <div className="flex-1 min-w-0 flex overflow-hidden">
      {/* ─── Список аккаунтов ─────────────────────────────────────── */}
      <section className="w-[340px] shrink-0 border-r border-token surface flex flex-col min-h-0">
        <div className="border-b border-token px-3 py-2 shrink-0 flex items-center justify-between">
          <div className="text-xs uppercase text-dim">
            Аккаунты · {total}
          </div>
          {listQ.loading && <span className="text-[11px] text-dim">…</span>}
        </div>

        <div className="flex-1 overflow-y-auto py-2 px-2 flex flex-col gap-0.5">
          {listQ.error && (
            <div className="alert-danger m-2 text-xs">
              {apiErrMsg(listQ.error, "Список не загрузился")}
              <button
                className="btn btn-sm ml-2"
                onClick={() => listQ.refetch()}
              >
                Повторить
              </button>
            </div>
          )}
          {!listQ.loading && !listQ.error && items.length === 0 && (
            <div className="px-3 py-6 text-xs text-dim text-center">
              На сервере нет аккаунтов.
            </div>
          )}
          {items.map((a) => (
            <AccountRow
              key={a.id}
              model={serverRowModel(a, serverId)}
              active={selectedId === a.id}
              onSelect={() => handleSelect(a.id)}
            />
          ))}
        </div>

        <TruncationNotice
          shown={items.length}
          total={total}
          className="mx-2 mb-2"
        />

        {canManage && (
          <div className="border-t border-token p-3 shrink-0 flex flex-col gap-2">
            <button
              className="btn btn-primary w-full flex items-center justify-center gap-2"
              onClick={handleStartCreate}
            >
              <Plus className="w-4 h-4" /> Создать аккаунт
            </button>
            <button
              className="btn w-full flex items-center justify-center gap-2"
              onClick={() => setLinking(true)}
            >
              <Link2 className="w-4 h-4" /> Привязать существующий
            </button>
          </div>
        )}
      </section>

      {/* ─── Side-panel ───────────────────────────────────────────── */}
      <section className="flex-1 min-w-0 overflow-y-auto p-5">
        {creating ? (
          <AccountCreateForm
            serverId={serverId}
            serverHostname={server?.hostname ?? serverId}
            onCancel={() => setCreating(false)}
            onCreated={(a) => {
              setCreating(false);
              setSelectedId(a.id);
              toast.success(`Аккаунт ${a.login} создан`);
              refresh();
            }}
            onError={(e) => toast.error(apiErrMsg(e, "Не удалось создать"))}
          />
        ) : selected ? (
          <AccountDetail
            key={selected.id}
            account={selected}
            serverId={serverId}
            canOperate={canOperate}
            canManage={canManage}
            onChanged={refresh}
            onClosed={() => setSelectedId(null)}
          />
        ) : (
          <div className="empty-card max-w-md mx-auto text-center mt-10">
            <User className="w-10 h-10 mx-auto text-dim mb-3" />
            <div className="text-sm text-dim">
              Выберите аккаунт слева, чтобы посмотреть детали и действия.
            </div>
          </div>
        )}
      </section>

      {linking && (
        <LinkAccountModal
          serverId={serverId}
          serverLabel={server?.display_name ?? server?.hostname ?? serverId}
          onClose={() => setLinking(false)}
          onLinked={(a) => {
            setLinking(false);
            setSelectedId(a.id);
            toast.success(`Аккаунт ${a.login} привязан к серверу`);
            refresh();
          }}
        />
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Учётки ВМ (read-only)
// ───────────────────────────────────────────────────────────────────────────

/**
 * Read-only список учёток, привязанных к ВМ (`GET /vms/{id}/accounts`). У ВМ
 * привязка задаётся при создании (мультиселект в форме create), поэтому CRUD
 * здесь нет — только просмотр. `present_on_vm` показывает дрейф: привязка есть,
 * а в госте учётки нет. Строки рисует общий `AccountRow` — та же презентация,
 * что и на серверном пути, только модель приходит из `vmRowModel`. В
 * mock-режиме данные из `@/mocks/vm`.
 */
function VmAccountsList({ vm, mock }: { vm: Vm; mock: boolean }) {
  const accountsQ = useQuery<VmAccount[]>(
    async () => {
      if (mock) return mockVmAccounts(vm);
      return await listVmAccounts(vm.id);
    },
    [vm.id, mock],
    { keepPreviousDataOnError: true },
  );
  const accounts = accountsQ.data ?? [];

  return (
    <div className="flex-1 min-w-0 overflow-y-auto p-5">
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-base flex items-center gap-2">
            <Users className="w-4 h-4 text-accent" /> Учётки
          </h3>
          <button
            className="btn btn-sm flex items-center gap-1"
            onClick={() => accountsQ.refetch()}
            disabled={accountsQ.loading}
            title="Обновить список учёток"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 ${accountsQ.loading ? "animate-spin" : ""}`}
            />
            Обновить
          </button>
        </div>
        <div className="text-xs text-dim mb-3">
          Учётки отдела, провижнящиеся OS-юзерами в госте ВМ. Привязка задаётся
          при создании ВМ.
        </div>

        {accountsQ.loading ? (
          <div className="text-xs text-dim">Загрузка…</div>
        ) : accountsQ.error && accounts.length === 0 ? (
          <div className="alert alert-danger flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5" />
            <div className="flex-1 text-xs">
              <div>
                {apiErrMsg(accountsQ.error, "Список учёток не загрузился")}
              </div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => accountsQ.refetch()}
              >
                Повторить
              </button>
            </div>
          </div>
        ) : accounts.length === 0 ? (
          <div className="text-xs text-dim">
            К ВМ не привязано ни одной учётки.
          </div>
        ) : (
          <div className="flex flex-col gap-0.5">
            {accounts.map((a) => (
              <AccountRow key={a.account_id} model={vmRowModel(a)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Список — одна строка (общая для сервера и ВМ)
// ───────────────────────────────────────────────────────────────────────────

type AccountBadge = { label: string; kind: "ok" | "warn" | "danger" | "" };

/**
 * Нормализованная строка учётки. И сервер, и ВМ приводят свою модель
 * (`ServerAccount` / `VmAccount`) к этому виду, чтобы список рисовал один и тот
 * же ряд: логин, вспомогательная подпись под ним и набор бейджей справа.
 */
interface AccountRowModel {
  /** Ключ строки и id для выбора: у сервера — `account.id`, у ВМ — `account_id`. */
  id: string;
  login: string;
  subtitle?: string;
  badges: AccountBadge[];
}

/** Серверная учётка → строка списка: scope + provision-бейдж, подпись rotated. */
function serverRowModel(a: ServerAccount, serverId: string): AccountRowModel {
  const scope = deriveScope(a);
  const prov = provisionBadge(a, serverId);
  return {
    id: a.id,
    login: a.login,
    subtitle: `rotated: ${fmtTs(a.password_rotated_at)}`,
    badges: [
      { label: scope, kind: scopeBadgeKind(scope) },
      { label: prov.label, kind: prov.kind },
    ],
  };
}

/** Учётка ВМ → строка списка: sudo + drift-бейдж, подпись с unix-группами. */
function vmRowModel(a: VmAccount): AccountRowModel {
  const groups = a.unix_groups.join(", ");
  const badges: AccountBadge[] = [];
  if (a.has_sudo) badges.push({ label: "sudo", kind: "warn" });
  badges.push(
    a.present_on_vm
      ? { label: "заведена", kind: "ok" }
      : { label: "дрейф", kind: "warn" },
  );
  return {
    id: a.account_id,
    login: a.login,
    subtitle: groups ? `группы: ${groups}` : undefined,
    badges,
  };
}

/**
 * Презентация одной строки списка учёток. Кликабельна только когда передан
 * `onSelect` (серверный путь с side-panel); ВМ отдаёт read-only ряд без
 * обработчика.
 */
function AccountRow({
  model,
  active,
  onSelect,
}: {
  model: AccountRowModel;
  active?: boolean;
  onSelect?: () => void;
}) {
  const body = (
    <div className="flex items-center gap-2">
      <User className={`w-4 h-4 ${active ? "text-accent" : "text-dim"}`} />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate mono">{model.login}</div>
        {model.subtitle && (
          <div className="text-[11px] text-dim truncate">{model.subtitle}</div>
        )}
      </div>
      {model.badges.map((b, i) => (
        <span key={i} className={`badge${b.kind ? ` badge-${b.kind}` : ""}`}>
          {b.label}
        </span>
      ))}
    </div>
  );

  if (!onSelect) {
    return <div className="cred-row">{body}</div>;
  }
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`cred-row text-left ${active ? "active" : ""}`}
    >
      {body}
    </button>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Detail
// ───────────────────────────────────────────────────────────────────────────

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 py-1 text-sm">
      <span className="text-xs text-dim w-44 shrink-0">{k}</span>
      <span className="flex-1 min-w-0 break-words">{v}</span>
    </div>
  );
}

function AccountDetail({
  account,
  serverId,
  canOperate,
  canManage,
  onChanged,
  onClosed,
}: {
  account: ServerAccount;
  serverId: string;
  canOperate: boolean;
  canManage: boolean;
  onChanged: () => void;
  onClosed: () => void;
}) {
  const toast = useToast();
  const navigate = useNavigate();
  const { confirm, prompt } = useConfirm();
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Разбивка последнего worker-rotate (dispatched / failed / partial_failure).
  const [rotateResult, setRotateResult] =
    useState<accountsApi.AccountRotateDispatchResponse | null>(null);

  const linkedUserLabel = useUserLabel(account.linked_user_id);
  const createdByLabel = useUserLabel(account.created_by);

  const scope = deriveScope(account);
  const prov = provisionBadge(account, serverId);
  const linked = account.server_ids.includes(serverId);
  // Discovered-аккаунт без сохранённого пароля — backend отбивает provision
  // ошибкой ACCOUNT_HAS_NO_PASSWORD. Кнопка остаётся доступной с подсказкой,
  // фактическая ошибка приходит уже сверху.
  const provisionDisabledReason =
    !canOperate
      ? "Нет прав на provision"
      : !linked
        ? "Сначала привяжите аккаунт к этому серверу"
        : null;

  const run = useCallback(
    async (fn: () => Promise<unknown>, ok: string) => {
      setErr(null);
      setPending(true);
      try {
        await fn();
        toast.success(ok);
        onChanged();
      } catch (e) {
        const msg = apiErrMsg(e);
        setErr(msg);
        toast.error(msg);
      } finally {
        setPending(false);
      }
    },
    [onChanged, toast],
  );

  // Worker-rotate отдельно от `run`: его ответ несёт per-task разбивку, которую
  // показываем под карточкой (а не просто toast'им).
  const runRotate = useCallback(
    async (query: { server_id?: string }) => {
      setErr(null);
      setRotateResult(null);
      setPending(true);
      try {
        const res = await accountsApi.rotateAccountWorker(account.id, query);
        setRotateResult(res);
        const queued = res.dispatched.length || res.tasks.length;
        const skipped = res.failed.length || res.skipped.length;
        if (res.partial_failure || skipped > 0) {
          toast.error(
            `Worker-rotate частичный: задач — ${queued}, пропущено — ${skipped}.`,
          );
        } else {
          toast.success(`Worker-rotate поставлен в очередь: задач — ${queued}.`);
        }
        onChanged();
      } catch (e) {
        const msg = apiErrMsg(e);
        setErr(msg);
        toast.error(msg);
      } finally {
        setPending(false);
      }
    },
    [account.id, onChanged, toast],
  );

  if (editing) {
    return (
      <AccountEditForm
        account={account}
        onCancel={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          toast.success("Аккаунт обновлён");
          onChanged();
        }}
        onError={(e) => toast.error(apiErrMsg(e, "Сохранение не удалось"))}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4 w-full">
      {/* ── Header ── */}
      <div className="card">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="font-semibold flex items-center gap-2 mono">
            <User className="w-4 h-4 text-accent" /> {account.login}
            <span
              className={`badge${scopeBadgeKind(scope) ? ` badge-${scopeBadgeKind(scope)}` : ""}`}
            >
              {scope}
            </span>
            <span className={`badge${prov.kind ? ` badge-${prov.kind}` : ""}`}>
              {prov.label}
            </span>
            {!account.is_active && <span className="badge badge-warn">неактивен</span>}
          </h3>
          <div className="flex items-center gap-2 flex-wrap">
            {canManage && (
              <button
                className="btn flex items-center gap-1"
                onClick={() => setEditing(true)}
                disabled={pending}
              >
                <Edit3 className="w-4 h-4" /> Изменить
              </button>
            )}
            <button
              className="btn"
              onClick={onClosed}
              type="button"
              title="Закрыть"
            >
              ×
            </button>
          </div>
        </div>

        {err && <div className="alert-danger mb-2 text-sm">{err}</div>}

        <div className="text-xs uppercase text-dim mb-2">Профиль</div>
        <StatRow k="account_id" v={<span className="mono">{account.id}</span>} />
        <StatRow k="login" v={<span className="mono">{account.login}</span>} />
        <StatRow k="scope" v={scope} />
        <StatRow k="source" v={account.source} />
        <StatRow k="has_sudo" v={account.has_sudo ? "yes" : "no"} />
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
          k="linked_user_id"
          v={
            account.linked_user_id ? (
              <span title={account.linked_user_id}>{linkedUserLabel}</span>
            ) : (
              <span className="text-dim italic">—</span>
            )
          }
        />
        <StatRow
          k="server_ids"
          v={
            <span className="text-xs text-dim">
              {account.server_ids.length} сервер(ов){" "}
              {linked ? "(включая этот)" : "(этот не в списке)"}
            </span>
          }
        />
        <StatRow k="created_at" v={<span className="mono">{fmtTs(account.created_at)}</span>} />
        <StatRow k="updated_at" v={<span className="mono">{fmtTs(account.updated_at)}</span>} />
        <StatRow
          k="password_rotated_at"
          v={<span className="mono">{fmtTs(account.password_rotated_at)}</span>}
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

      {/* ── Пароль ── */}
      <PasswordRevealCard account={account} canReveal={canOperate} />

      {/* ── Provision на этом сервере ── */}
      <div className="card">
        <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
          <Power className="w-3 h-3" /> Provision на сервере
        </div>
        <div className="text-xs text-dim mb-3">
          Запускает worker-таск (`useradd` / `usermod` / `userdel`) только на
          текущем сервере. Полная отвязка делается через Unbind ниже.
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn flex items-center gap-1"
            disabled={pending || !!provisionDisabledReason}
            title={provisionDisabledReason ?? "useradd на боксе"}
            onClick={() =>
              run(
                () => accountsApi.provisionOnHost(serverId, account.id),
                "Provision-task поставлен в очередь",
              )
            }
          >
            <Power className="w-4 h-4" /> Provision
          </button>
          <button
            className="btn flex items-center gap-1"
            disabled={pending || !canOperate || !linked}
            title={
              !canOperate
                ? "Нет прав"
                : !linked
                  ? "Аккаунт не привязан к этому серверу"
                  : "usermod синхронизирует атрибуты"
            }
            onClick={() =>
              run(
                () => accountsApi.updateOnHost(serverId, account.id),
                "Update-on-host-task поставлен в очередь",
              )
            }
          >
            <RotateCw className="w-4 h-4" /> Update on host
          </button>
          <button
            className="btn btn-danger flex items-center gap-1"
            disabled={pending || !canOperate || !linked}
            title={
              !canOperate
                ? "Нет прав"
                : !linked
                  ? "Аккаунт не привязан к этому серверу"
                  : "userdel — оставит запись связки до Unbind"
            }
            onClick={async () => {
              if (
                !(await confirm({
                  message: `Удалить OS-пользователя ${account.login} с этого сервера?`,
                  confirmLabel: "Deprovision",
                  danger: true,
                }))
              )
                return;
              run(
                () => accountsApi.deprovisionOnHost(serverId, account.id),
                "Deprovision-task поставлен в очередь",
              );
            }}
          >
            <Trash2 className="w-4 h-4" /> Deprovision
          </button>
        </div>
      </div>

      {/* ── Rotate password ── */}
      <div className="card">
        <div className="text-xs uppercase text-dim mb-2 flex items-center gap-2">
          <KeyRound className="w-3 h-3" /> Ротация пароля
        </div>
        <div className="text-xs text-dim mb-3">
          Sync — генерирует новый пароль в БД, на боксы не уезжает. Worker —
          ставит SSH-task на все или только на этот сервер. Plaintext клиенту
          не возвращается.
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn flex items-center gap-1"
            disabled={pending || !canOperate}
            title={canOperate ? "Только БД" : "Нет прав"}
            onClick={() =>
              run(
                () => accountsApi.rotateAccountUserInitiated(account.id),
                "Пароль ротирован в БД",
              )
            }
          >
            <KeyRound className="w-4 h-4" /> Rotate (sync, БД)
          </button>
          <button
            className="btn flex items-center gap-1"
            disabled={pending || !canOperate || !linked}
            title={
              !canOperate
                ? "Нет прав"
                : !linked
                  ? "Аккаунт не привязан к этому серверу"
                  : "Worker: SSH chpasswd на этом сервере"
            }
            onClick={() => runRotate({ server_id: serverId })}
          >
            <RotateCw className="w-4 h-4" /> Worker: этот сервер
          </button>
          <button
            className="btn flex items-center gap-1"
            disabled={pending || !canOperate}
            title={canOperate ? "Worker: на все привязанные серверы" : "Нет прав"}
            onClick={async () => {
              if (
                !(await confirm({
                  message: `Запустить worker-rotate на все ${account.server_ids.length} серверов?`,
                  confirmLabel: "Запустить",
                }))
              )
                return;
              runRotate({});
            }}
          >
            <RotateCw className="w-4 h-4" /> Worker: все
          </button>
        </div>
        {rotateResult && (
          <div className="border-t border-token mt-3 pt-3">
            <div className="text-[11px] uppercase text-dim mb-2">
              Результат worker-rotate
            </div>
            <RotateDispatchResult
              result={rotateResult}
              onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
            />
          </div>
        )}
      </div>

      {/* ── Danger zone ── */}
      <div className="card" style={{ borderColor: "rgba(244,135,113,0.3)" }}>
        <div className="text-xs uppercase text-danger mb-2 flex items-center gap-2">
          <AlertTriangle className="w-3 h-3" /> Опасная зона
        </div>
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="text-sm flex-1 min-w-[200px]">
              <div className="font-medium">Отвязать от этого сервера</div>
              <div className="text-xs text-dim">
                Снимает связку аккаунт ↔ сервер и сразу удаляет OS-юзера с бокса,
                если он там стоял (userdel). Можно отвязать и последний сервер —
                карточка аккаунта останется в БД без серверов.
              </div>
            </div>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={pending || !canOperate || !linked}
              title={
                !canOperate
                  ? "Нет прав"
                  : !linked
                    ? "Аккаунт не привязан к этому серверу"
                    : undefined
              }
              onClick={async () => {
                if (
                  !(await confirm({
                    message: `Отвязать аккаунт ${account.login} от этого сервера?`,
                    confirmLabel: "Отвязать",
                    danger: true,
                  }))
                )
                  return;
                run(
                  () => accountsApi.unbindAccountServer(account.id, serverId),
                  "Аккаунт отвязан от сервера",
                );
              }}
            >
              <Unlink className="w-4 h-4" /> Unbind
            </button>
          </div>

          <div className="flex items-center justify-between gap-3 flex-wrap border-t border-token pt-3">
            <div className="text-sm flex-1 min-w-[200px]">
              <div className="font-medium">Удалить аккаунт</div>
              <div className="text-xs text-dim">
                Hard-delete во всех серверах. OS-аккаунт на боксах не сносится;
                для этого сначала Deprovision на каждом.
              </div>
            </div>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={pending || !canManage}
              title={canManage ? undefined : "Нет прав на удаление"}
              onClick={async () => {
                const { ok } = await prompt({
                  title: "Удалить аккаунт",
                  message: `Удалить аккаунт ${account.login}? Операция необратима.`,
                  reason: true,
                  reasonLabel: "Причина удаления",
                  reasonRequired: true,
                  confirmLabel: "Удалить",
                  danger: true,
                });
                if (!ok) return;
                run(async () => {
                  await accountsApi.deleteAccount(account.id);
                  onClosed();
                }, `Аккаунт ${account.login} удалён`);
              }}
            >
              <Trash2 className="w-4 h-4" /> Удалить
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Password reveal
// ───────────────────────────────────────────────────────────────────────────

/**
 * Reveal-блок пароля аккаунта.
 *
 * По умолчанию замаскирован. По клику «показать» дёргает карточку аккаунта
 * (`getAccount`), декодит `password_b64` → plaintext. Если backend вернул
 * `password_b64 = null` — это либо нет грантa `view_password`, либо у аккаунта
 * нет сохранённого пароля (discovered без apply); показываем понятную причину,
 * а не пустоту. 429 (reveal-rate-limit) гасит кнопку на retry-окно.
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

  // Сбрасываем раскрытое при смене аккаунта.
  useEffect(() => {
    setPlain(null);
    setReason(null);
    setThrottleUntil(0);
  }, [account.id]);

  useEffect(() => {
    if (throttleUntil <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [throttleUntil]);

  const throttleLeft = Math.max(0, Math.ceil((throttleUntil - now) / 1000));
  const shown = plain !== null;

  // Discovered-аккаунт без ротации пароля скорее всего не имеет ciphertext'а в
  // БД — backend вернёт password_b64=null даже держателю view_password. Это
  // подсказка ещё до запроса; финальную причину всё равно даёт backend.
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
        // Невалидный base64 — отдадим как есть, чтобы не терять значение.
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

  function handleHide() {
    setPlain(null);
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
        Пароль хранится зашифрованным (AES-256-GCM). Показ требует права
        view_password.
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
              onClick={handleHide}
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
                : "Раскрыть пароль"
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
// Forms
// ───────────────────────────────────────────────────────────────────────────

function AccountCreateForm({
  serverId,
  serverHostname,
  onCancel,
  onCreated,
  onError,
}: {
  serverId: string;
  serverHostname: string;
  onCancel: () => void;
  onCreated: (a: ServerAccount) => void;
  onError: (e: unknown) => void;
}) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [scope, setScope] = useState<Scope>("service");
  const [hasSudo, setHasSudo] = useState(false);
  const [groups, setGroups] = useState("");
  const [shell, setShell] = useState("");
  const [homeDir, setHomeDir] = useState("");
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    const loginValue = login.trim();
    if (!loginValue) return;
    const unixGroups = groups
      .split(",")
      .map((g) => g.trim())
      .filter(Boolean);
    const passwordValue = password.trim();
    const validationErr =
      validateLogin(loginValue) ??
      validateUnixGroups(unixGroups) ??
      (passwordValue ? validateAccountPassword(passwordValue) : null);
    if (validationErr) {
      setErr(validationErr);
      return;
    }
    setErr(null);
    setPending(true);
    try {
      const body: accountsApi.ServerAccountCreateInput = {
        server_ids: [serverId],
        login: loginValue,
        password: passwordValue || null,
        has_sudo: hasSudo,
        unix_groups: unixGroups,
        shell: shell.trim() || null,
        home_dir: homeDir.trim() || null,
      };
      // Scope — derived field, не отправляется в backend; кладём его
      // отметку через linked_user_id только в режиме `personal` (поле
      // на форме пока не вводится — оставим backend дефолт).
      const created = await accountsApi.createAccount(body);
      onCreated(created);
    } catch (e) {
      const msg = accountPasswordPolicyError(e) ?? apiErrMsg(e, "Создание не удалось");
      setErr(msg);
      onError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card w-full">
      <h3 className="font-semibold mb-3 flex items-center gap-2 mono">
        <Plus className="w-4 h-4 text-accent" /> Новый аккаунт на{" "}
        {serverHostname}
      </h3>
      {err && <div className="alert-danger mb-2 text-sm">{err}</div>}
      <form onSubmit={submit} className="flex flex-col gap-3">
        <FormRow label="login *">
          <input
            className="input mono"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            required
            maxLength={128}
            placeholder="dbos-svc"
          />
        </FormRow>
        <FormRow
          label="password"
          hint={`оставьте пустым — backend сгенерирует случайный; иначе ${PASSWORD_POLICY_HINT}`}
        >
          <input
            className="input mono"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="—"
            autoComplete="new-password"
          />
        </FormRow>
        <FormRow label="scope" hint="визуальный признак (UI)">
          <select
            className="input"
            value={scope}
            onChange={(e) => setScope(e.target.value as Scope)}
          >
            <option value="service">service</option>
            <option value="shared">shared</option>
            <option value="personal">personal</option>
          </select>
        </FormRow>
        <FormRow label="sudo">
          <label className="inline-flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={hasSudo}
              onChange={(e) => setHasSudo(e.target.checked)}
            />
            <span>выдать sudo</span>
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
        <FormRow label="home_dir">
          <input
            className="input mono"
            value={homeDir}
            onChange={(e) => setHomeDir(e.target.value)}
            placeholder="/home/dbos-svc"
          />
        </FormRow>
        <div className="mt-3 flex gap-2 justify-end">
          <button type="button" className="btn" onClick={onCancel}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={pending || !login.trim()}
          >
            {pending ? "Создаём…" : "Создать"}
          </button>
        </div>
      </form>
    </div>
  );
}

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
  const [homeDir, setHomeDir] = useState(account.home_dir ?? "");
  const [linkedUserId, setLinkedUserId] = useState(account.linked_user_id ?? "");
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
        home_dir: homeDir.trim() || null,
        linked_user_id: linkedUserId.trim() || null,
      };
      await accountsApi.updateAccount(account.id, body);
      onSaved();
    } catch (e) {
      const msg = apiErrMsg(e, "Сохранение не удалось");
      setErr(msg);
      onError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card w-full">
      <h3 className="font-semibold mb-3 flex items-center gap-2 mono">
        <Edit3 className="w-4 h-4 text-accent" /> Edit · {account.login}
      </h3>
      {err && <div className="alert-danger mb-2 text-sm">{err}</div>}
      <form onSubmit={submit} className="flex flex-col gap-3">
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
        <FormRow label="unix_groups" hint="csv">
          <input
            className="input mono"
            value={groups}
            onChange={(e) => setGroups(e.target.value)}
          />
        </FormRow>
        <FormRow label="shell">
          <input
            className="input mono"
            value={shell}
            onChange={(e) => setShell(e.target.value)}
          />
        </FormRow>
        <FormRow label="home_dir">
          <input
            className="input mono"
            value={homeDir}
            onChange={(e) => setHomeDir(e.target.value)}
          />
        </FormRow>
        <FormRow label="linked_user_id" hint="auth_service user id или пусто">
          <input
            className="input mono"
            value={linkedUserId}
            onChange={(e) => setLinkedUserId(e.target.value)}
          />
        </FormRow>
        <div className="mt-3 flex gap-2 justify-end">
          <button type="button" className="btn" onClick={onCancel}>
            Отмена
          </button>
          <button type="submit" className="btn btn-primary" disabled={pending}>
            {pending ? "Сохраняем…" : "Сохранить"}
          </button>
        </div>
      </form>
    </div>
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
