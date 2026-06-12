/**
 * Manage-вкладка карточки сервера.
 *
 * Большой админский блок, разбитый на карточки:
 *  - Lifecycle: Prepare / Inventory sync / OS sync / Users inventory.
 *  - Busy: захват / освобождение с reason'ом.
 *  - Cancel running task: скрыт, пока нет видимого task_id (свежий `Server`
 *    его не несёт; появится, когда подсоединим список `tasks`).
 *  - Администрирование: inline-CRUD каталога `os_versions` (видно
 *    только `account_admin`'у).
 *  - Danger zone: hard-delete сервера с reason'ом.
 *
 * Все мутации сопровождаются confirm + reason там, где это уместно, и
 * gate'ятся через `@/lib/rbac` хелперы. Backend перепроверит права —
 * client-side фильтр прячет только лишнее.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Play,
  RefreshCw,
  Pause,
  PlayCircle,
  Trash2,
  Settings,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Plus,
  Edit3,
  Save,
  XCircle,
  ListTree,
  UserCheck,
} from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { apiErrMsg } from "@/api/client";
import { formatMskShort } from "@/lib/datetime";
import { isDepAdmin } from "@/lib/rbac";
import {
  clearBusy,
  deleteServer,
  inventorySync,
  prepareServer,
  setBusy,
} from "@/api/server/servers";
import { usersInventory } from "@/api/server/misc";
import { listAccounts } from "@/api/server/accounts";
import {
  createOsVersion,
  deleteOsVersion,
  listOsVersions,
  osSync,
  updateOsVersion,
} from "@/api/server/osVersions";
import { FormRow } from "@/pages/admin/services/_inline";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  OsVersion,
  Server,
  ServerAccount,
} from "@/api/server/types";

interface Props {
  serverId: string;
  server?: Server;
  onServerUpdated?: (next: Server) => void;
  onDeleted?: () => void;
}

function utf8ToB64(s: string): string {
  // unicode-safe base64 для bootstrap-кред (на бэке symmetric base64-decoded).
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function isDepAdminOfServer(
  persona: ReturnType<typeof usePersona>["persona"],
  server: Server | undefined,
): boolean {
  return (
    isDepAdmin(persona) && !!server && persona.dept_id === server.department_id
  );
}

function canManageBasic(
  persona: ReturnType<typeof usePersona>["persona"],
  server: Server | undefined,
): boolean {
  if (persona.service_roles.server === "admin") return true;
  if (persona.service_roles.server === "operator") return true;
  if (isDepAdminOfServer(persona, server)) return true;
  return false;
}

/**
 * Аккаунты сервера, видимые текущей persona (грубый client-side фильтр —
 * тот же контракт, что и в `tabs/console.tsx`). Backend перепроверит при
 * fetch'е пароля; здесь только UX, чтобы picker не показывал заведомо
 * недоступные строки.
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

export function ManageTab({ server, onServerUpdated, onDeleted }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const [busy, setBusyLocal] = useState<string | null>(null);
  const [current, setCurrent] = useState<Server | undefined>(server);
  // Когда родитель прислал свежий объект (мутация в соседней вкладке) —
  // подхватываем его, чтобы не залипнуть на устаревшей локальной копии.
  useEffect(() => {
    setCurrent(server);
  }, [server]);
  const view = current ?? server;

  // Любая lifecycle/busy-мутация возвращает обновлённый Server: правим
  // локальную копию и поднимаем наверх, чтобы header ServerDetail и соседние
  // вкладки увидели новый busy/status без перезагрузки.
  const applyServer = useCallback(
    (next: Server) => {
      setCurrent(next);
      onServerUpdated?.(next);
    },
    [onServerUpdated],
  );

  const allowBasic = canManageBasic(persona, view);
  // os_version CRUD и server:delete — только admin-плоскость (dep_admin своего
  // dept либо server.admin). operator их не получает по дефолтной матрице.
  const allowOsCatalog =
    isDepAdminOfServer(persona, view) ||
    persona.service_roles.server === "admin";
  const allowDelete =
    isDepAdminOfServer(persona, view) ||
    persona.service_roles.server === "admin";

  // Аккаунты сервера — нужны inventory/users SSH-задачам на неуправляемом
  // сервере: worker заходит под self-сессией по паролю аккаунта. Управляемый
  // сервер ходит по ключу — picker тогда не обязателен (backend сам None'ит).
  const accountsQ = useQuery(
    () => listAccounts({ server_id: view!.id, limit: 200 }),
    [view?.id],
    { enabled: !!view },
  );
  const accounts = useMemo<ServerAccount[]>(() => {
    const data = accountsQ.data as
      | OffsetPaginatedResponse<ServerAccount>
      | CursorPaginatedResponse<ServerAccount>
      | undefined;
    return data?.items ?? [];
  }, [accountsQ.data]);
  const accessibleAccounts = useMemo(
    () => filterAccessibleAccounts(accounts, persona),
    [accounts, persona],
  );
  const [accountId, setAccountId] = useState<string>("");
  // account_id шлём только на неуправляемом сервере; на managed worker идёт
  // по ключу, передавать пусто.
  const inventoryAccountId =
    view && !view.is_managed && accountId ? accountId : undefined;

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
    if (busy) return null;
    setBusyLocal(label);
    try {
      const res = await fn();
      toast.success(`${label}: OK`);
      return res;
    } catch (e) {
      toast.error(apiErrMsg(e, `${label} не удалось`));
      return null;
    } finally {
      setBusyLocal(null);
    }
  }

  return (
    <div className="p-5 flex flex-col gap-4">
      <LifecycleCard
        server={view}
        allowed={allowBasic}
        busyLabel={busy}
        accounts={accessibleAccounts}
        accountId={accountId}
        onAccountChange={setAccountId}
        accountsLoading={accountsQ.loading}
        onPrepare={async () => {
          if (!view) return;
          if (
            !window.confirm(
              `Запустить prepare для ${view.hostname}? Действие сбрасывает bootstrap-креды на сервере и инициирует management-цикл.`,
            )
          )
            return;
          const username = window.prompt("Bootstrap username:");
          if (!username) return;
          const password = window.prompt("Bootstrap password:");
          if (!password) return;
          await run("prepare", () =>
            prepareServer(view.id, {
              username_b64: utf8ToB64(username),
              password_b64: utf8ToB64(password),
            }),
          );
        }}
        onInventory={async () => {
          if (!view) return;
          await run("inventory_sync", () =>
            inventorySync(view.id, { account_id: inventoryAccountId }),
          );
        }}
        onOsSync={async () => {
          if (!view) return;
          const id = window.prompt(
            "os_version_id (`osv_…`) или пусто для сброса:",
            view.os_version_id ?? "",
          );
          if (id === null) return;
          const next = await run("os_sync", () =>
            osSync(view.id, {
              os_version_id: id.trim() ? id.trim() : null,
            }),
          );
          if (next) applyServer(next);
        }}
        onUsersInventory={async () => {
          if (!view) return;
          await run("users_inventory", () =>
            usersInventory(view.id, { account_id: inventoryAccountId }),
          );
        }}
      />

      <BusyCard
        server={view}
        allowed={allowBasic}
        busyLabel={busy}
        onSetBusy={async (reason) => {
          if (!view) return;
          const next = await run("busy_set", () =>
            setBusy(view.id, { reason }),
          );
          if (next) applyServer(next);
        }}
        onClearBusy={async () => {
          if (!view) return;
          if (!window.confirm("Снять busy-захват с сервера?")) return;
          const next = await run("busy_clear", () => clearBusy(view.id));
          if (next) applyServer(next);
        }}
      />

      {/* Cancel running task: пока в `Server` нет видимого task_id, блок
          закрыт. Когда подключим список tasks из server_service — раскроется. */}

      {allowOsCatalog && <OsCatalogCard />}

      {view && (
        <DangerCard
          server={view}
          allowed={allowDelete}
          busyLabel={busy}
          onDelete={async (reason) => {
            await run("delete_server", async () => {
              await deleteServer(view.id, { reason });
              onDeleted?.();
            });
          }}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Lifecycle
// ─────────────────────────────────────────────────────────────────────────────

function LifecycleCard({
  server,
  allowed,
  busyLabel,
  accounts,
  accountId,
  onAccountChange,
  accountsLoading,
  onPrepare,
  onInventory,
  onOsSync,
  onUsersInventory,
}: {
  server: Server | undefined;
  allowed: boolean;
  busyLabel: string | null;
  accounts: ServerAccount[];
  accountId: string;
  onAccountChange: (id: string) => void;
  accountsLoading: boolean;
  onPrepare: () => Promise<void>;
  onInventory: () => Promise<void>;
  onOsSync: () => Promise<void>;
  onUsersInventory: () => Promise<void>;
}) {
  const disabled = !allowed || busyLabel !== null || !server;
  // На managed-сервере inventory идёт по ключу — account picker не нужен.
  // На неуправляемом worker заходит под аккаунтом по паролю: показываем
  // селектор. Пусто → backend возьмёт дефолтный привязанный аккаунт.
  const needsAccount = !!server && !server.is_managed && allowed;
  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <Settings className="w-4 h-4 text-accent" /> Lifecycle
      </h3>
      {needsAccount && (
        <div className="flex items-center gap-2 flex-wrap mb-3">
          <label className="text-xs text-dim">
            SSH-аккаунт для inventory
          </label>
          <select
            className="surface-2 border border-token rounded px-2 py-1 text-sm"
            value={accountId}
            onChange={(e) => onAccountChange(e.target.value)}
            disabled={disabled || accountsLoading}
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
            {accountsLoading
              ? "загружаем…"
              : accounts.length === 0
                ? "нет привязанных аккаунтов — inventory вернёт 422"
                : "пусто → первый аккаунт с паролем"}
          </span>
        </div>
      )}
      <div className="flex gap-2 flex-wrap">
        <button
          className="btn btn-primary flex items-center gap-1"
          disabled={disabled}
          onClick={onPrepare}
          title={
            allowed
              ? "Bootstrap management-цикла"
              : "Нет прав на prepare"
          }
        >
          <Play className="w-4 h-4" />
          {busyLabel === "prepare" ? "Запускаем…" : "Prepare"}
        </button>
        <button
          className="btn flex items-center gap-1"
          disabled={disabled}
          onClick={onInventory}
        >
          <RefreshCw
            className={`w-4 h-4 ${busyLabel === "inventory_sync" ? "animate-spin" : ""}`}
          />
          Inventory sync
        </button>
        <button
          className="btn flex items-center gap-1"
          disabled={disabled}
          onClick={onOsSync}
        >
          <RefreshCw
            className={`w-4 h-4 ${busyLabel === "os_sync" ? "animate-spin" : ""}`}
          />
          OS sync
        </button>
        <button
          className="btn flex items-center gap-1"
          disabled={disabled}
          onClick={onUsersInventory}
        >
          <UserCheck className="w-4 h-4" />
          Users inventory
        </button>
      </div>
      {!allowed && (
        <div className="text-[11px] text-dim italic mt-3">
          Нет прав на lifecycle-операции (нужна роль server.operator+ или
          dep_admin своего департамента).
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Busy toggle
// ─────────────────────────────────────────────────────────────────────────────

function BusyCard({
  server,
  allowed,
  busyLabel,
  onSetBusy,
  onClearBusy,
}: {
  server: Server | undefined;
  allowed: boolean;
  busyLabel: string | null;
  onSetBusy: (reason: string) => Promise<void>;
  onClearBusy: () => Promise<void>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [reason, setReason] = useState("");
  if (!server) {
    return (
      <div className="card text-sm text-dim">Busy: нет данных по серверу.</div>
    );
  }

  const isBusy = server.busy_state !== "free" || !!server.busy_note;
  const disabled = !allowed || busyLabel !== null;

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <Pause className="w-4 h-4 text-accent" /> Busy lease
      </h3>
      {isBusy ? (
        <div className="alert flex items-start gap-2">
          <Pause className="w-4 h-4 mt-0.5 text-warn" />
          <div className="flex-1 text-xs">
            <div>
              Сервер занят:{" "}
              <span className="mono">{server.busy_state}</span>
              {server.busy_user_id && (
                <>
                  {" · "}юзер{" "}
                  <span className="mono">{server.busy_user_id}</span>
                </>
              )}
            </div>
            {server.busy_note && (
              <div className="text-dim mt-1">
                Причина: <span className="mono">{server.busy_note}</span>
              </div>
            )}
            {server.busy_since && (
              <div className="text-dim text-[11px] mt-1">
                с <span className="mono">{formatMskShort(server.busy_since)}</span>
              </div>
            )}
          </div>
          {allowed && (
            <button
              className="btn flex items-center gap-1"
              disabled={disabled}
              onClick={onClearBusy}
              title="Освободить сервер"
            >
              <PlayCircle className="w-4 h-4" />
              Clear busy
            </button>
          )}
        </div>
      ) : showForm ? (
        <div className="flex flex-col gap-2 max-w-xl">
          <FormRow label="reason / purpose">
            <input
              className="input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="например, ручной debug-цикл"
            />
          </FormRow>
          <div className="flex gap-2 justify-end">
            <button
              className="btn"
              onClick={() => {
                setShowForm(false);
                setReason("");
              }}
              disabled={busyLabel !== null}
            >
              Отмена
            </button>
            <button
              className="btn btn-primary"
              disabled={disabled || !reason.trim()}
              onClick={async () => {
                await onSetBusy(reason.trim());
                setShowForm(false);
                setReason("");
              }}
            >
              Mark busy
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3 flex-wrap">
          <div className="text-xs text-dim flex-1">
            Сервер свободен. Захват блокирует параллельные тесты до тех пор,
            пока ты или другой админ не снимешь lease.
          </div>
          {allowed && (
            <button
              className="btn btn-primary flex items-center gap-1"
              disabled={disabled}
              onClick={() => setShowForm(true)}
            >
              <Pause className="w-4 h-4" />
              Mark busy
            </button>
          )}
        </div>
      )}
      {!allowed && (
        <div className="text-[11px] text-dim italic mt-3">
          Нет прав на busy-операции.
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Администрирование — OS Versions catalog (account_admin)
// ─────────────────────────────────────────────────────────────────────────────

function OsCatalogCard() {
  const [open, setOpen] = useState(false);
  return (
    <div className="card">
      <button
        className="w-full flex items-center gap-2 text-left"
        onClick={() => setOpen((v) => !v)}
        type="button"
      >
        {open ? (
          <ChevronDown className="w-4 h-4 text-dim" />
        ) : (
          <ChevronRight className="w-4 h-4 text-dim" />
        )}
        <ListTree className="w-4 h-4 text-accent" />
        <span className="font-semibold text-base">Администрирование</span>
        <span className="text-xs text-dim">· OS Versions catalog</span>
      </button>
      {open && (
        <div className="mt-4">
          <OsCatalogBody />
        </div>
      )}
    </div>
  );
}

function OsCatalogBody() {
  const toast = useToast();
  const q = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit: 200 }),
    [],
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const items = useMemo(() => q.data?.items ?? [], [q.data]);

  async function handleDelete(v: OsVersion) {
    if (
      !window.confirm(
        `Удалить ${v.name}? Если хоть один сервер на неё ссылается — backend вернёт 409.`,
      )
    )
      return;
    setPendingId(v.id);
    try {
      await deleteOsVersion(v.id);
      toast.success(`OS version ${v.name} удалён`);
      q.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    } finally {
      setPendingId(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="flex-1 text-xs text-dim">
          Глобальный каталог OS-версий. Используется в `servers.os_version_id`,
          delete с FK-ссылками блокируется на бэке.
        </div>
        <button
          className="btn btn-primary flex items-center gap-1"
          disabled={creating || editingId !== null}
          onClick={() => setCreating(true)}
        >
          <Plus className="w-4 h-4" /> Создать
        </button>
      </div>

      {q.loading && <div className="text-xs text-dim">Загружаем каталог…</div>}
      {q.error && (
        <div className="alert alert-danger text-xs">
          {apiErrMsg(q.error, "Каталог не загрузился")}
        </div>
      )}

      {creating && (
        <OsVersionForm
          mode="new"
          onCancel={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            q.refetch();
          }}
        />
      )}

      <div className="surface-2 border border-token rounded overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] uppercase text-dim border-b border-token">
              <th className="text-left px-3 py-2 font-medium">name</th>
              <th className="text-left px-3 py-2 font-medium">description</th>
              <th className="text-left px-3 py-2 font-medium">repositories</th>
              <th className="text-left px-3 py-2 font-medium">discovered</th>
              <th className="text-right px-3 py-2 font-medium">действия</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !q.loading ? (
              <tr>
                <td
                  colSpan={5}
                  className="px-3 py-4 text-xs text-dim text-center"
                >
                  Каталог пуст.
                </td>
              </tr>
            ) : (
              items.map((v) =>
                editingId === v.id ? (
                  <tr key={v.id} className="border-b border-token">
                    <td colSpan={5} className="px-3 py-3">
                      <OsVersionForm
                        mode="edit"
                        initial={v}
                        onCancel={() => setEditingId(null)}
                        onSaved={() => {
                          setEditingId(null);
                          q.refetch();
                        }}
                      />
                    </td>
                  </tr>
                ) : (
                  <tr
                    key={v.id}
                    className="border-b border-token last:border-b-0"
                  >
                    <td className="px-3 py-1.5 mono text-xs">{v.name}</td>
                    <td className="px-3 py-1.5 text-xs">
                      {v.description ?? <span className="text-dim">—</span>}
                    </td>
                    <td className="px-3 py-1.5 text-xs mono text-dim">
                      {v.repositories.length === 0
                        ? "—"
                        : `${v.repositories.length} шт`}
                    </td>
                    <td className="px-3 py-1.5 text-xs text-dim mono">
                      {v.discovered_at}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <div className="inline-flex gap-1">
                        <button
                          className="btn btn-ghost flex items-center gap-1"
                          disabled={pendingId !== null || editingId !== null}
                          onClick={() => setEditingId(v.id)}
                          title="Edit"
                        >
                          <Edit3 className="w-4 h-4" />
                        </button>
                        <button
                          className="btn btn-danger flex items-center gap-1"
                          disabled={pendingId !== null}
                          onClick={() => handleDelete(v)}
                          title="Delete"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ),
              )
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OsVersionForm({
  mode,
  initial,
  onCancel,
  onSaved,
}: {
  mode: "new" | "edit";
  initial?: OsVersion;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [reposText, setReposText] = useState(
    initial?.repositories.join("\n") ?? "",
  );
  const [pending, setPending] = useState(false);

  async function submit() {
    if (!name.trim()) {
      toast.error("name обязателен");
      return;
    }
    const repos = reposText
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    setPending(true);
    try {
      if (mode === "new") {
        await createOsVersion({
          name: name.trim(),
          description: description.trim() ? description.trim() : null,
          repositories: repos,
        });
        toast.success(`OS version ${name.trim()} создан`);
      } else if (initial) {
        await updateOsVersion(initial.id, {
          name: name.trim(),
          description: description.trim() ? description.trim() : null,
          repositories: repos,
        });
        toast.success(`OS version ${name.trim()} обновлён`);
      }
      onSaved();
    } catch (e) {
      toast.error(apiErrMsg(e, "Сохранение не удалось"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 max-w-2xl">
      <FormRow label="name">
        <input
          className="input mono"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={pending}
        />
      </FormRow>
      <FormRow label="description">
        <input
          className="input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={pending}
        />
      </FormRow>
      <FormRow label="repositories (по одному на строку)">
        <textarea
          className="input mono text-xs"
          rows={3}
          value={reposText}
          onChange={(e) => setReposText(e.target.value)}
          disabled={pending}
          placeholder="https://repo.astralinux.ru/astra/stable/orel/repository&#10;…"
        />
      </FormRow>
      <div className="flex gap-2 justify-end">
        <button
          className="btn flex items-center gap-1"
          onClick={onCancel}
          disabled={pending}
        >
          <XCircle className="w-4 h-4" /> Отмена
        </button>
        <button
          className="btn btn-primary flex items-center gap-1"
          onClick={submit}
          disabled={pending}
        >
          <Save className="w-4 h-4" />
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Danger zone
// ─────────────────────────────────────────────────────────────────────────────

function DangerCard({
  server,
  allowed,
  busyLabel,
  onDelete,
}: {
  server: Server;
  allowed: boolean;
  busyLabel: string | null;
  onDelete: (reason: string) => Promise<void>;
}) {
  if (!allowed) return null;
  return (
    <div
      className="card"
      style={{ border: "1px solid var(--danger, #b91c1c)" }}
    >
      <div className="text-sm font-semibold flex items-center gap-2 text-danger mb-2">
        <AlertTriangle className="w-4 h-4" /> Danger zone
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 text-xs text-dim">
          Hard-delete сервера каскадом удаляет IPMI-контроллер, диски, привязки
          к аккаунтам и историю задач. Действие необратимо.
        </div>
        <button
          className="btn btn-danger flex items-center gap-1"
          disabled={busyLabel !== null}
          onClick={async () => {
            if (
              !window.confirm(
                `Удалить сервер ${server.hostname} полностью? Действие необратимо.`,
              )
            )
              return;
            const reason = window.prompt(
              "Причина удаления (decommission / wrong-record / …):",
            );
            if (!reason) return;
            await onDelete(reason);
          }}
        >
          <Trash2 className="w-4 h-4" />
          {busyLabel === "delete_server" ? "Удаляем…" : "Delete server"}
        </button>
      </div>
    </div>
  );
}

