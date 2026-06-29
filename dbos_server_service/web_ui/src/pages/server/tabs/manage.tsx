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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
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
  ListChecks,
  UserCheck,
  KeyRound,
  X,
} from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { apiErrMsg } from "@/api/client";
import { formatMskShort } from "@/lib/datetime";
import { isDepAdmin } from "@/lib/rbac";
import { useUserLabel } from "@/lib/labels";
import { toBase64 } from "@/lib/base64";
import { sshKeyFingerprint } from "@/lib/sshFingerprint";
import {
  clearBusy,
  deleteServer,
  getServer,
  inventorySync,
  prepareServer,
  rotateManagementCredentials,
  setBusy,
} from "@/api/server/servers";
import { usersInventory } from "@/api/server/misc";
import { useTaskOutcome, type TrackedTask } from "@/api/server/useTaskOutcome";
import { listAccounts } from "@/api/server/accounts";
import {
  createOsVersion,
  deleteOsVersion,
  listOsVersions,
  osSync,
  updateOsVersion,
} from "@/api/server/osVersions";
import { FormRow } from "@/pages/admin/services/_inline";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import {
  filterAccessibleAccounts,
  reservedErrorMessage,
} from "@/pages/server/_serverShared";
import { BootstrapCredsModal } from "./_bootstrapCredsModal";
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

export function ManageTab({ server, onServerUpdated, onDeleted }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const { confirm } = useConfirm();
  const [busy, setBusyLocal] = useState<string | null>(null);
  // Поллинг исхода lifecycle-задач (prepare / inventory / users-inventory):
  // worker может закрыть их FAILED (битые bootstrap-креды, недоступный BMC),
  // и причину надо показать прямо здесь, не гоня юзера в /worker.
  const taskOutcome = useTaskOutcome();
  // Отдельный трекер для ротации управляющих кред: у неё свой баннер в
  // mgmt-карточке, чтобы не пересекаться с lifecycle-исходом.
  const rotateOutcome = useTaskOutcome();
  const rotateHandledRef = useRef<string | null>(null);
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

  // Когда worker подтвердил ротацию (задача succeeded) — перечитываем карточку,
  // чтобы подхватить новый fingerprint/rotated_at и снять pending. Ref гасит
  // повторные refetch'и: applyServer меняет `view`, иначе эффект зациклится.
  useEffect(() => {
    const t = rotateOutcome.tracked;
    if (!t || t.polling || t.status !== "succeeded") return;
    if (rotateHandledRef.current === t.taskId) return;
    rotateHandledRef.current = t.taskId;
    if (view) {
      getServer(view.id)
        .then((next) => applyServer(next))
        .catch(() => {});
    }
  }, [rotateOutcome.tracked, view, applyServer]);

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
  // prepare собирает bootstrap-креды через модалку (выбор аккаунта или ручной
  // ввод с masked-полем пароля), а не через window.prompt.
  const [credsModalOpen, setCredsModalOpen] = useState(false);
  const [osSyncOpen, setOsSyncOpen] = useState(false);

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
    if (busy) return null;
    setBusyLocal(label);
    try {
      const res = await fn();
      toast.success(`${label}: OK`);
      return res;
    } catch (e) {
      // Деструктив на чужой брони → 409 SERVER_RESERVED: показываем понятный
      // текст про бронь. Прочие ошибки — обычным envelope'ом.
      toast.error(reservedErrorMessage(e, `${label} не удалось`));
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
        outcome={taskOutcome.tracked}
        onCancelled={taskOutcome.reset}
        onPrepare={async () => {
          if (!view) return;
          if (
            !(await confirm({
              title: "Запустить prepare",
              message: `Запустить prepare для ${view.hostname}? Действие сбрасывает bootstrap-креды на сервере и инициирует management-цикл.`,
              confirmLabel: "Запустить",
            }))
          )
            return;
          setCredsModalOpen(true);
        }}
        onInventory={async () => {
          if (!view) return;
          taskOutcome.reset();
          const res = await run("inventory_sync", () =>
            inventorySync(view.id),
          );
          if (res) taskOutcome.track("inventory_sync", res.task_id, res.status);
        }}
        onOsSync={() => {
          if (!view) return;
          setOsSyncOpen(true);
        }}
        onUsersInventory={async () => {
          if (!view) return;
          taskOutcome.reset();
          const res = await run("users_inventory", () =>
            usersInventory(view.id),
          );
          if (res) taskOutcome.track("users_inventory", res.task_id, res.status);
        }}
      />

      <ManagementCredsCard
        server={view}
        allowed={allowBasic}
        busyLabel={busy}
        outcome={rotateOutcome.tracked}
        onCancelled={rotateOutcome.reset}
        onRotate={async () => {
          if (!view) return;
          if (
            !(await confirm({
              title: "Ротировать управляющие креды",
              message: `Сгенерировать новую управляющую SSH-пару и пароль для ${view.hostname}? Старый материал отзывается после применения на сервере. Действие чувствительное, пишется в аудит как CRITICAL.`,
              confirmLabel: "Ротировать",
              danger: true,
            }))
          )
            return;
          rotateOutcome.reset();
          rotateHandledRef.current = null;
          const res = await run("mgmt_rotate", () =>
            rotateManagementCredentials(view.id),
          );
          if (res) {
            rotateOutcome.track("mgmt_rotate", res.task_id, res.status);
            // Оптимистично метим pending: задача только поставлена в очередь,
            // refetch по succeeded позже вернёт реальное состояние.
            applyServer({ ...view, mgmt_creds_pending_apply: true });
          }
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
          if (!(await confirm({ message: "Снять busy-захват с сервера?" })))
            return;
          const next = await run("busy_clear", () => clearBusy(view.id));
          if (next) applyServer(next);
        }}
      />

      {/* Cancel running task: пока в `Server` нет видимого task_id, блок
          закрыт. Когда подключим список tasks из server_service — раскроется. */}

      {allowOsCatalog && <OsCatalogCard />}

      {view && credsModalOpen && (
        <BootstrapCredsModal
          hostname={view.hostname}
          accounts={accessibleAccounts}
          accountsLoading={accountsQ.loading}
          onClose={() => setCredsModalOpen(false)}
          onSubmit={async (creds) => {
            taskOutcome.reset();
            const body =
              creds.mode === "account"
                ? { account_id: creds.accountId }
                : {
                    username_b64: toBase64(creds.username),
                    password_b64: toBase64(creds.password),
                    ...(creds.sshPrivateKey.trim()
                      ? { ssh_private_key_b64: toBase64(creds.sshPrivateKey) }
                      : {}),
                  };
            const res = await run("prepare", () =>
              prepareServer(view.id, body),
            );
            setCredsModalOpen(false);
            if (res) taskOutcome.track("prepare", res.task_id, res.status);
          }}
        />
      )}

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

      {view && osSyncOpen && (
        <OsSyncModal
          currentOsVersionId={view.os_version_id ?? ""}
          onClose={() => setOsSyncOpen(false)}
          onSubmit={async (osVersionId) => {
            // os-sync синхронный: backend сразу UPDATE'ит строку и отдаёт
            // Server, worker-задачи нет — поллить нечего.
            const next = await run("os_sync", () =>
              osSync(view.id, { os_version_id: osVersionId || null }),
            );
            setOsSyncOpen(false);
            if (next) applyServer(next);
          }}
        />
      )}
    </div>
  );
}

/**
 * Привязка сервера к OS-версии из каталога: дропдаун по именам (значение —
 * `osv_*` id), пустое = сброс привязки. Заменяет ручной ввод сырого id.
 */
function OsSyncModal({
  currentOsVersionId,
  onClose,
  onSubmit,
}: {
  currentOsVersionId: string;
  onClose: () => void;
  onSubmit: (osVersionId: string) => void | Promise<void>;
}) {
  const q = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit: 200 }),
    [],
  );
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const [selected, setSelected] = useState(currentOsVersionId);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(selected));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header flex items-center justify-between">
          <div className="text-sm font-semibold">OS sync</div>
          <button
            className="btn btn-ghost p-1"
            onClick={onClose}
            aria-label="Закрыть"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="modal-body flex flex-col gap-3">
          <div className="text-xs text-dim">
            Привязать сервер к OS-версии из каталога. Пусто = сбросить привязку.
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">OS version</span>
            {q.loading ? (
              <div className="text-xs text-dim">Загрузка каталога…</div>
            ) : (
              <select
                className="input"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                <option value="">— не задана —</option>
                {items.map((v) => (
                  <option key={v.id} value={v.id} title={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            )}
          </label>
          <div className="flex items-center gap-2 mt-1">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={submitting}
            >
              {submitting ? "Сохраняем…" : "Сохранить"}
            </button>
            <button type="button" className="btn" onClick={onClose}>
              Отмена
            </button>
          </div>
        </form>
      </div>
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
  outcome,
  onCancelled,
  onPrepare,
  onInventory,
  onOsSync,
  onUsersInventory,
}: {
  server: Server | undefined;
  allowed: boolean;
  busyLabel: string | null;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  onPrepare: () => void;
  onInventory: () => Promise<void>;
  onOsSync: () => void;
  onUsersInventory: () => Promise<void>;
}) {
  const disabled = !allowed || busyLabel !== null || !server;
  // Инвентаризация идёт по SSH под управляющим ключом — до prepare заходить
  // нечем, backend вернёт 409 PREPARE_REQUIRED. Гейтим кнопки и подсказываем,
  // что сначала надо prepare. OS sync — локальный UPDATE, prepare не требует.
  const prepared = !!server && server.is_managed;
  const inventoryDisabled = disabled || !prepared;
  const prepareHint = prepared
    ? undefined
    : "Сначала запустите prepare — инвентаризация ходит по управляющему ключу";
  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <h3 className="font-semibold text-base flex items-center gap-2">
          <Settings className="w-4 h-4 text-accent" /> Lifecycle
        </h3>
        {server && (
          <Link
            to={`/worker?server_id=${encodeURIComponent(server.id)}`}
            className="btn btn-ghost flex items-center gap-1 text-xs"
            title="Открыть worker-задачи этого сервера"
          >
            <ListChecks className="w-3.5 h-3.5" /> Задачи сервера
          </Link>
        )}
      </div>
      {server && !prepared && (
        <div className="text-[11px] text-dim italic mb-3">
          Сервер не подготовлен (нет management-пользователя). Инвентаризация
          станет доступна после успешного prepare.
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
          disabled={inventoryDisabled}
          onClick={onInventory}
          title={prepareHint}
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
          disabled={inventoryDisabled}
          onClick={onUsersInventory}
          title={prepareHint}
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
      {outcome && (
        <TaskOutcomeBanner
          outcome={outcome}
          className="mt-3"
          onCancelled={onCancelled}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Управляющие креды (per-server, фича #3)
// ─────────────────────────────────────────────────────────────────────────────

function ManagementCredsCard({
  server,
  allowed,
  busyLabel,
  outcome,
  onCancelled,
  onRotate,
}: {
  server: Server | undefined;
  allowed: boolean;
  busyLabel: string | null;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  onRotate: () => Promise<void>;
}) {
  const prepared = !!server && server.is_managed;
  // pending — либо backend ещё применяет ротацию (`mgmt_creds_pending_apply`),
  // либо мы поллим только что задиспатченную задачу.
  const pending =
    !!server?.mgmt_creds_pending_apply || (outcome?.polling ?? false);
  const disabled =
    !allowed || busyLabel !== null || !prepared || pending || !server;

  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const pubKey = server?.mgmt_ssh_public_key ?? null;
  useEffect(() => {
    let alive = true;
    sshKeyFingerprint(pubKey).then((fp) => {
      if (alive) setFingerprint(fp);
    });
    return () => {
      alive = false;
    };
  }, [pubKey]);

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <KeyRound className="w-4 h-4 text-accent" /> Управляющие креды
        {pending && (
          <span className="badge badge-warn text-[11px]">
            ротация применяется…
          </span>
        )}
      </h3>

      {!prepared ? (
        <div className="text-[11px] text-dim italic">
          Управляющая пара появляется после prepare — на неподготовленном сервере
          ротировать нечего.
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 text-xs mb-3">
            <dt className="text-dim">fingerprint</dt>
            <dd className="mono break-all">
              {fingerprint ? (
                fingerprint
              ) : (
                <span className="text-dim" title="server_service ещё не отдаёт mgmt_ssh_public_key в карточке сервера">
                  —
                </span>
              )}
            </dd>
            <dt className="text-dim">rotated_at</dt>
            <dd className="mono">
              {server?.mgmt_creds_rotated_at ? (
                formatMskShort(server.mgmt_creds_rotated_at)
              ) : (
                <span className="text-dim">—</span>
              )}
            </dd>
          </dl>

          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 text-xs text-dim">
              Генерирует новую управляющую SSH-пару и пароль, применяет их на
              сервере через worker и отзывает старый материал. Чувствительная
              операция — CRITICAL-аудит.
            </div>
            {allowed && (
              <button
                className="btn btn-danger flex items-center gap-1"
                disabled={disabled}
                onClick={onRotate}
                title={
                  prepared
                    ? "Ротировать управляющую пару и пароль"
                    : "Сначала prepare"
                }
              >
                <KeyRound className="w-4 h-4" />
                {pending
                  ? "Ротация идёт…"
                  : busyLabel === "mgmt_rotate"
                    ? "Запускаем…"
                    : "Ротировать управляющие креды"}
              </button>
            )}
          </div>
        </>
      )}

      {!allowed && (
        <div className="text-[11px] text-dim italic mt-3">
          Нет прав на ротацию управляющих кред (нужна роль server.operator+ или
          dep_admin своего департамента).
        </div>
      )}

      {outcome && (
        <TaskOutcomeBanner
          outcome={outcome}
          className="mt-3"
          onCancelled={onCancelled}
        />
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
  const reserverLabel = useUserLabel(server?.busy_user_id);
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
                  <span title={server.busy_user_id}>{reserverLabel}</span>
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
  const { confirm } = useConfirm();
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
      !(await confirm({
        title: "Удалить OS-версию",
        message: `Удалить ${v.name}? Если хоть один сервер на неё ссылается — backend вернёт 409.`,
        confirmLabel: "Удалить",
        danger: true,
      }))
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

      <TruncationNotice shown={items.length} total={q.data?.total ?? null} />
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
  const { prompt } = useConfirm();
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
            const { ok, reason } = await prompt({
              title: "Удалить сервер",
              message: `Удалить сервер ${server.hostname} полностью? Действие необратимо.`,
              reason: true,
              reasonLabel: "Причина удаления",
              reasonPlaceholder: "decommission / wrong-record / …",
              reasonRequired: true,
              confirmLabel: "Удалить",
              danger: true,
            });
            if (!ok) return;
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

