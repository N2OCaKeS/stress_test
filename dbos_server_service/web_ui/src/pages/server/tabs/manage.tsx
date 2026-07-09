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
  Trash2,
  Settings,
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
  Eraser,
  X,
  ArrowUpCircle,
  MonitorPlay,
  ShieldCheck,
  Network,
} from "lucide-react";
import { useQuery } from "@/api/auth/useQuery";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { apiErrMsg } from "@/api/client";
import { formatMskShort } from "@/lib/datetime";
import { isDepAdmin, canPrepareVmsHub } from "@/lib/rbac";
import { useUserLabel } from "@/lib/labels";
import { sshKeyFingerprint } from "@/lib/sshFingerprint";
import {
  astraUpdate,
  clearBusy,
  deleteServer,
  getServer,
  inventorySync,
  prepareServer,
  rotateManagementCredentials,
  setBusy,
} from "@/api/server/servers";
import { usersInventory } from "@/api/server/misc";
import {
  astraUpdateVm,
  deleteVm,
  getAvailableIps,
  listVmIpPools,
  prepareVm,
  prepareVmsHub,
  rotateVmMgmtCreds,
  setVmNetwork,
  teardownVmsHub,
  type Vm,
  type VmIpPool,
  type VmNetworkMode,
} from "@/api/server/vms";
import { useTaskOutcome, type TrackedTask } from "@/api/server/useTaskOutcome";
import { listAccounts } from "@/api/server/accounts";
import {
  createOsVersion,
  deleteOsVersion,
  listOsVersions,
  osSync,
  updateOsVersion,
} from "@/api/server/osVersions";
import {
  MOCK_AVAILABLE_IPS,
  MOCK_VM_IP_POOLS,
  MOCK_VM_OS_VERSIONS,
} from "@/mocks/vm";
import { FormRow } from "@/pages/admin/services/_inline";
import { TruncationNotice } from "@/components/ui/TruncationNotice";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import {
  filterAccessibleAccounts,
  reservedErrorMessage,
} from "@/pages/server/_serverShared";
import { BookingCard } from "@/components/entity/manage/BookingCard";
import { DangerZoneCard } from "@/components/entity/manage/DangerZoneCard";
import { BootstrapCredsModal } from "./_bootstrapCredsModal";
import { CleanModal } from "./_cleanModal";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  OsVersion,
  Server,
  ServerAccount,
  TaskDispatchResponse,
} from "@/api/server/types";
import type { EntityRef } from "./_entity";

interface Props {
  serverId: string;
  server?: Server;
  onServerUpdated?: (next: Server) => void;
  onDeleted?: () => void;
  /** Ссылка на сущность: при `kind==="vm"` вкладка рендерит управление ВМ. */
  entity?: EntityRef;
  /** Локальное обновление карточки после мутации (общий контракт вкладок). */
  onEntityUpdated?: (next: Server | Vm) => void;
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

export function ManageTab(props: Props) {
  const { entity, onDeleted, onEntityUpdated } = props;
  // ВМ рендерит собственный набор карточек (mgmt-креды + удаление). Серверный
  // путь — прежний. Диспетчер без хуков, чтобы порядок хуков не зависел от режима.
  if (entity?.kind === "vm") {
    return (
      <VmManageView
        vm={entity.vm}
        mock={entity.mock}
        canManage={entity.canManage}
        onChanged={entity.onChanged}
        onEntityUpdated={onEntityUpdated}
        onDeleted={onDeleted}
      />
    );
  }
  return <ServerManageTab {...props} />;
}

function ServerManageTab({ server, onServerUpdated, onDeleted }: Props) {
  const { persona } = usePersona();
  const toast = useToast();
  const { confirm, prompt } = useConfirm();
  const [busy, setBusyLocal] = useState<string | null>(null);
  // Поллинг исхода lifecycle-задач (prepare / inventory / users-inventory):
  // worker может закрыть их FAILED (битые bootstrap-креды, недоступный BMC),
  // и причину надо показать прямо здесь, не гоня юзера в /worker.
  const taskOutcome = useTaskOutcome();
  // Отдельный трекер для ротации управляющих кред: у неё свой баннер в
  // mgmt-карточке, чтобы не пересекаться с lifecycle-исходом.
  const rotateOutcome = useTaskOutcome();
  const rotateHandledRef = useRef<string | null>(null);
  // Обновление ОС Astra: свой трекер, чтобы по succeeded перечитать карточку
  // (backend снял updating-блокировку, сменил версию и запустил inventory).
  const astraOutcome = useTaskOutcome();
  const astraHandledRef = useRef<string | null>(null);
  // Подготовка/разбор VMS-hub: свой трекер, по succeeded перечитываем карточку
  // (backend флипает is_vms_hub на callback после завершения задачи).
  const vmsHubOutcome = useTaskOutcome();
  const vmsHubHandledRef = useRef<string | null>(null);
  const [current, setCurrent] = useState<Server | undefined>(server);
  // Когда родитель прислал свежий объект (мутация в соседней вкладке) —
  // подхватываем его, чтобы не залипнуть на устаревшей локальной копии.
  useEffect(() => {
    setCurrent(server);
  }, [server]);
  const view = current ?? server;
  const reserverLabel = useUserLabel(view?.busy_user_id);

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

  // Обновление ОС завершилось — перечитываем карточку: сервер уже свободен
  // (updating снят), версия и факты обновлены. Ref гасит повторный refetch.
  useEffect(() => {
    const t = astraOutcome.tracked;
    if (!t || t.polling || t.status !== "succeeded") return;
    if (astraHandledRef.current === t.taskId) return;
    astraHandledRef.current = t.taskId;
    if (view) {
      getServer(view.id)
        .then((next) => applyServer(next))
        .catch(() => {});
    }
  }, [astraOutcome.tracked, view, applyServer]);

  // Подготовка VMS-hub завершилась — перечитываем карточку, чтобы подхватить
  // is_vms_hub/vms_hub_prepared_at. Ref гасит повторный refetch.
  useEffect(() => {
    const t = vmsHubOutcome.tracked;
    if (!t || t.polling || t.status !== "succeeded") return;
    if (vmsHubHandledRef.current === t.taskId) return;
    vmsHubHandledRef.current = t.taskId;
    if (view) {
      getServer(view.id)
        .then((next) => applyServer(next))
        .catch(() => {});
    }
  }, [vmsHubOutcome.tracked, view, applyServer]);

  const allowBasic = canManageBasic(persona, view);
  const allowVmsHub = canPrepareVmsHub(persona);
  // Пока идёт обновление ОС — сервер под системной блокировкой: все
  // управляющие операции backend отобьёт 409 SERVER_UPDATING. Гейтим кнопки
  // на клиенте, чтобы не слать заведомо отбиваемые запросы.
  const updating = view?.busy_state === "updating";
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
  const [cleanOpen, setCleanOpen] = useState(false);

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
        locked={updating}
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

      <VmsHubCard
        server={view}
        allowed={allowVmsHub}
        locked={updating}
        busyLabel={busy}
        outcome={vmsHubOutcome.tracked}
        onCancelled={vmsHubOutcome.reset}
        onPrepare={async () => {
          if (!view) return;
          if (
            !(await confirm({
              title: "Подготовить как VMS-hub",
              message: `Подготовить ${view.hostname} как VMS-hub? Установит libvirt/kvm, настроит мост br0 и storage-pool, скачает образы каталога.`,
              confirmLabel: "Подготовить",
            }))
          )
            return;
          vmsHubOutcome.reset();
          vmsHubHandledRef.current = null;
          const res = await run("prepare_vms_hub", () =>
            prepareVmsHub(view.id),
          );
          if (res)
            vmsHubOutcome.track("prepare_vms_hub", res.task_id, res.status);
        }}
        onTeardown={async () => {
          if (!view) return;
          const { ok, reason } = await prompt({
            title: "Разобрать VMS-hub",
            message: `Разобрать VMS-hub ${view.hostname}? Будут снесены libvirt-конфигурация, мост br0 и storage-pool; сервер вернётся в обычное состояние. ВМ на хабе быть не должно.`,
            reason: true,
            reasonLabel: "Причина",
            reasonRequired: true,
            confirmLabel: "Разобрать",
            danger: true,
          });
          if (!ok) return;
          vmsHubOutcome.reset();
          vmsHubHandledRef.current = null;
          const res = await run("teardown_vms_hub", () =>
            teardownVmsHub(view.id, { reason: reason.trim() }),
          );
          if (res)
            vmsHubOutcome.track("teardown_vms_hub", res.task_id, res.status);
        }}
      />

      <AstraUpdateCard
        server={view}
        allowed={allowBasic}
        locked={updating}
        busyLabel={busy}
        outcome={astraOutcome.tracked}
        onCancelled={astraOutcome.reset}
        onUpdate={async (osVersionId) => {
          if (!view) return;
          if (
            !(await confirm({
              title: "Обновить ОС Astra",
              message: `Обновить ОС на ${view.hostname}? sources.list будет полностью перезаписан репозиториями выбранной версии, затем пойдёт apt update && astra-update. На время обновления все операции с сервером блокируются.`,
              confirmLabel: "Обновить",
              danger: true,
            }))
          )
            return;
          astraOutcome.reset();
          astraHandledRef.current = null;
          const res = await run("astra_update", () =>
            astraUpdate(view.id, { os_version_id: osVersionId }),
          );
          if (res) {
            astraOutcome.track("astra_update", res.task_id, res.status);
            // Оптимистично метим updating — карточка сразу заблокирует кнопки,
            // refetch по succeeded вернёт реальное состояние.
            applyServer({
              ...view,
              busy_state: "updating",
              busy_note: "Обновление ОС Astra",
            });
          }
        }}
      />

      <ManagementCredsCard
        server={view}
        allowed={allowBasic}
        locked={updating}
        busyLabel={busy}
        outcome={rotateOutcome.tracked}
        onCancelled={rotateOutcome.reset}
        onRotate={async () => {
          if (!view) return;
          if (
            !(await confirm({
              title: "Ротировать управляющие креды",
              message: `Сгенерировать новую управляющую SSH-пару и пароль для ${view.hostname}? Старый материал отзывается после применения на сервере.`,
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

      <BookingCard
        entityWord="сервер"
        reserved={!!view && (view.busy_state !== "free" || !!view.busy_note)}
        stateLabel={view?.busy_state ?? "—"}
        note={view?.busy_note}
        reserverLabel={
          view?.busy_user_id ? (
            <span title={view.busy_user_id}>юзер {reserverLabel}</span>
          ) : undefined
        }
        since={view?.busy_since ? formatMskShort(view.busy_since) : undefined}
        canManage={allowBasic}
        foreign={!!view?.busy_user_id && view.busy_user_id !== persona.id}
        busy={busy !== null}
        onReserve={async (reason) => {
          if (!view) return;
          const next = await run("busy_set", () =>
            setBusy(view.id, { reason }),
          );
          if (next) applyServer(next);
        }}
        onRelease={async () => {
          if (!view) return;
          const foreign =
            !!view.busy_user_id && view.busy_user_id !== persona.id;
          const message = foreign
            ? "Сервер забронирован другим пользователем. Снять бронь принудительно? После освобождения его сможет занять любой."
            : "Снять busy-захват с сервера?";
          if (!(await confirm({ message }))) return;
          const next = await run("busy_clear", () => clearBusy(view.id));
          if (next) applyServer(next);
        }}
      />

      {/* Cancel running task: пока в `Server` нет видимого task_id, блок
          закрыт. Когда подключим список tasks из server_service — раскроется. */}

      <CleanCard
        allowed={allowBasic}
        locked={updating}
        busyLabel={busy}
        onClean={() => setCleanOpen(true)}
      />

      {allowOsCatalog && <OsCatalogCard />}

      {view && credsModalOpen && (
        <BootstrapCredsModal
          hostname={view.hostname}
          accounts={accessibleAccounts}
          accountsLoading={accountsQ.loading}
          onClose={() => setCredsModalOpen(false)}
          onSubmit={async (body) => {
            taskOutcome.reset();
            const res = await run("prepare", () =>
              prepareServer(view.id, body),
            );
            setCredsModalOpen(false);
            if (res) taskOutcome.track("prepare", res.task_id, res.status);
          }}
        />
      )}

      {view && allowDelete && (
        <DangerZoneCard
          buttonLabel={busy === "delete_server" ? "Удаляем…" : "Удалить сервер"}
          busy={busy !== null}
          description="Hard-delete сервера каскадом удаляет IPMI-контроллер, диски, привязки к аккаунтам и историю задач. Действие необратимо."
          onDelete={async () => {
            const { ok, reason } = await prompt({
              title: "Удалить сервер",
              message: `Удалить сервер ${view.hostname} полностью? Действие необратимо.`,
              reason: true,
              reasonLabel: "Причина удаления",
              reasonPlaceholder: "decommission / wrong-record / …",
              reasonRequired: true,
              confirmLabel: "Удалить",
              danger: true,
            });
            if (!ok) return;
            await run("delete_server", async () => {
              await deleteServer(view.id, { reason });
              onDeleted?.();
            });
          }}
        />
      )}

      {view && cleanOpen && (
        <CleanModal
          serverId={view.id}
          hostname={view.hostname}
          accounts={accessibleAccounts}
          accountsLoading={accountsQ.loading}
          currentOsVersionId={view.os_version_id}
          onClose={() => setCleanOpen(false)}
          onDone={() => {
            // Clean мог сменить os_version / снять управление — перечитываем
            // карточку, чтобы соседние вкладки и header увидели свежий объект.
            getServer(view.id)
              .then((next) => applyServer(next))
              .catch(() => {});
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
            <span className="text-dim text-xs">Версия ОС</span>
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
  locked = false,
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
  /** Сервер под системной блокировкой обновления ОС — операции заблокированы. */
  locked?: boolean;
  busyLabel: string | null;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  onPrepare: () => void;
  onInventory: () => Promise<void>;
  onOsSync: () => void;
  onUsersInventory: () => Promise<void>;
}) {
  const disabled = !allowed || locked || busyLabel !== null || !server;
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
          <Settings className="w-4 h-4 text-accent" /> Жизненный цикл
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
      {locked && (
        <div className="alert alert-warn text-xs mb-3 flex items-center gap-2">
          <ArrowUpCircle className="w-4 h-4" />
          Идёт обновление ОС — операции с сервером заблокированы до завершения.
        </div>
      )}
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
// Виртуализация — подготовка/разбор VMS-hub
// ─────────────────────────────────────────────────────────────────────────────

function VmsHubCard({
  server,
  allowed,
  locked = false,
  busyLabel,
  outcome,
  onCancelled,
  onPrepare,
  onTeardown,
}: {
  server: Server | undefined;
  /** Персона вправе готовить/разбирать hub (`canPrepareVmsHub`). */
  allowed: boolean;
  /** Сервер под системной блокировкой обновления ОС — операции недоступны. */
  locked?: boolean;
  busyLabel: string | null;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  onPrepare: () => Promise<void>;
  onTeardown: () => Promise<void>;
}) {
  // Карточка имеет смысл только когда сервер умеет виртуализацию или уже hub.
  if (!server || (!server.virtualization && !server.is_vms_hub)) return null;
  const isHub = !!server.is_vms_hub;
  const disabled = !allowed || locked || busyLabel !== null;

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <MonitorPlay className="w-4 h-4 text-accent" /> Виртуализация
        {isHub && <span className="badge badge-ok text-[11px]">VMS-hub</span>}
      </h3>

      {isHub ? (
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex-1 text-xs text-dim">
            Сервер подготовлен как VMS-hub: развёрнуты libvirt/kvm, мост br0 и
            storage-pool. Виртуальные машины этого хаба — в разделе
            «Виртуализация».
          </div>
          <Link
            to={`/vm?hub=${encodeURIComponent(server.id)}`}
            className="btn flex items-center gap-1"
            title="Открыть ВМ этого hub'а"
          >
            <MonitorPlay className="w-4 h-4" /> ВМ этого hub'а
          </Link>
          {allowed && (
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={disabled}
              onClick={onTeardown}
              title="Разобрать VMS-hub — снести libvirt/мост/pool"
            >
              <Trash2 className="w-4 h-4" />
              {busyLabel === "teardown_vms_hub"
                ? "Запускаем…"
                : "Разобрать VMS-hub"}
            </button>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex-1 text-xs text-dim">
            Сервер поддерживает виртуализацию (KVM). Подготовка развернёт
            libvirt/kvm, мост br0 и storage-pool и скачает образы каталога —
            после этого на нём можно создавать ВМ.
          </div>
          {allowed && (
            <button
              className="btn btn-primary flex items-center gap-1"
              disabled={disabled}
              onClick={onPrepare}
              title="Подготовить сервер как VMS-hub"
            >
              <MonitorPlay className="w-4 h-4" />
              {busyLabel === "prepare_vms_hub"
                ? "Запускаем…"
                : "Подготовить как VMS-hub"}
            </button>
          )}
        </div>
      )}

      {!allowed && (
        <div className="text-[11px] text-dim italic mt-3">
          Нет прав на подготовку/разбор VMS-hub (нужна роль server.operator+ или
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
  locked = false,
  busyLabel,
  outcome,
  onCancelled,
  onRotate,
}: {
  server: Server | undefined;
  allowed: boolean;
  /** Сервер под системной блокировкой обновления ОС — ротация недоступна. */
  locked?: boolean;
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
    !allowed || locked || busyLabel !== null || !prepared || pending || !server;

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
              сервере через worker и отзывает старый материал.
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
// Обновление ОС Astra (astra_update)
// ─────────────────────────────────────────────────────────────────────────────

function AstraUpdateCard({
  server,
  allowed,
  locked = false,
  busyLabel,
  outcome,
  onCancelled,
  onUpdate,
}: {
  server: Server | undefined;
  allowed: boolean;
  /** Сервер уже под updating-блокировкой — кнопка «идёт обновление». */
  locked?: boolean;
  busyLabel: string | null;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  onUpdate: (osVersionId: string) => Promise<void>;
}) {
  const prepared = !!server && server.is_managed;
  const q = useQuery<OffsetPaginatedResponse<OsVersion>>(
    () => listOsVersions({ limit: 200 }),
    [],
  );
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const [selected, setSelected] = useState("");
  const disabled =
    !allowed || locked || busyLabel !== null || !prepared || !server;

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <ArrowUpCircle className="w-4 h-4 text-accent" /> Обновление ОС Astra
        {locked && (
          <span className="badge badge-warn text-[11px]">идёт обновление…</span>
        )}
      </h3>

      {!prepared ? (
        <div className="text-[11px] text-dim italic">
          Обновление идёт по управляющему ключу — сначала выполните prepare.
        </div>
      ) : (
        <>
          <div className="text-xs text-dim mb-3">
            Обновляет сервер до выбранной версии ОС из каталога: полностью
            перезаписывает <span className="mono">/etc/apt/sources.list</span>{" "}
            репозиториями версии и выполняет{" "}
            <span className="mono">apt update &amp;&amp; astra-update</span>. На
            время обновления любые операции с сервером блокируются.
          </div>
          <div className="flex items-end gap-2 flex-wrap">
            <label className="flex flex-col gap-1 text-sm flex-1 min-w-[200px]">
              <span className="text-dim text-xs">Целевая версия ОС</span>
              {q.loading ? (
                <div className="text-xs text-dim">Загрузка каталога…</div>
              ) : (
                <select
                  className="input"
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                  disabled={disabled}
                >
                  <option value="">— выберите версию —</option>
                  {items.map((v) => (
                    <option key={v.id} value={v.id} title={v.id}>
                      {v.name}
                      {v.repositories.length === 0 ? " (нет репозиториев)" : ""}
                    </option>
                  ))}
                </select>
              )}
            </label>
            {allowed && (
              <button
                className="btn btn-danger flex items-center gap-1"
                disabled={disabled || !selected}
                onClick={() => onUpdate(selected)}
                title={
                  prepared
                    ? "Обновить ОС до выбранной версии"
                    : "Сначала prepare"
                }
              >
                <ArrowUpCircle className="w-4 h-4" />
                {locked
                  ? "Обновление идёт…"
                  : busyLabel === "astra_update"
                    ? "Запускаем…"
                    : "Обновить ОС"}
              </button>
            )}
          </div>
        </>
      )}

      {!allowed && (
        <div className="text-[11px] text-dim italic mt-3">
          Нет прав на обновление ОС (нужна роль server.operator+ или dep_admin
          своего департамента).
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
      toast.success(`OS-версия ${v.name} удалена`);
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
              <th className="text-left px-3 py-2 font-medium">Название</th>
              <th className="text-left px-3 py-2 font-medium">Описание</th>
              <th className="text-left px-3 py-2 font-medium">Репозитории</th>
              <th className="text-left px-3 py-2 font-medium">Обнаружен</th>
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
                          title="Изменить"
                        >
                          <Edit3 className="w-4 h-4" />
                        </button>
                        <button
                          className="btn btn-danger flex items-center gap-1"
                          disabled={pendingId !== null}
                          onClick={() => handleDelete(v)}
                          title="Удалить"
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
        toast.success(`OS-версия ${name.trim()} создана`);
      } else if (initial) {
        await updateOsVersion(initial.id, {
          name: name.trim(),
          description: description.trim() ? description.trim() : null,
          repositories: repos,
        });
        toast.success(`OS-версия ${name.trim()} обновлена`);
      }
      onSaved();
    } catch (e) {
      toast.error(apiErrMsg(e, "Сохранение не удалось"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
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
// Clean — оркестрация очистки после переустановки ОС
// ─────────────────────────────────────────────────────────────────────────────

function CleanCard({
  allowed,
  locked = false,
  busyLabel,
  onClean,
}: {
  allowed: boolean;
  /** Сервер под системной блокировкой обновления ОС — очистка недоступна. */
  locked?: boolean;
  busyLabel: string | null;
  onClean: () => void;
}) {
  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <Eraser className="w-4 h-4 text-accent" /> Очистка после переустановки ОС
      </h3>
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex-1 text-xs text-dim">
          Сборная операция для сервера после переустановки ОС: отвязать все
          учётки, заново забутстрапить управление, сменить версию ОС и/или
          запустить inventory. Действия выбираются в модалке.
        </div>
        {allowed && (
          <button
            className="btn btn-danger flex items-center gap-1"
            disabled={locked || busyLabel !== null}
            onClick={onClean}
            title="Очистка сервера после переустановки ОС"
          >
            <Eraser className="w-4 h-4" /> Очистить
          </button>
        )}
      </div>
      {!allowed && (
        <div className="text-[11px] text-dim italic mt-3">
          Нет прав на очистку (нужна роль server.operator+ или dep_admin своего
          департамента).
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ВМ — управление (подготовка/mgmt-креды + удаление)
// ─────────────────────────────────────────────────────────────────────────────

function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
}

function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-dim text-xs">{k}</dt>
      <dd className={mono ? "mono" : undefined}>{v}</dd>
    </>
  );
}

/**
 * Manage-вкладка для ВМ. Бронь (reserve/release) сюда не входит — она живёт в
 * шапке карточки ВМ; здесь только подготовка/ротация управляющих кред и
 * удаление домена.
 */
function VmManageView({
  vm,
  mock,
  canManage,
  onChanged,
  onEntityUpdated,
  onDeleted,
}: {
  vm: Vm;
  mock: boolean;
  canManage: boolean;
  onChanged: () => void;
  onEntityUpdated?: (next: Server | Vm) => void;
  onDeleted?: () => void;
}) {
  const toast = useToast();
  const { prompt } = useConfirm();
  const [busy, setBusy] = useState(false);

  if (!canManage) {
    return (
      <div className="p-5">
        <div className="text-[11px] text-dim italic">
          Нет прав на управление этой ВМ.
        </div>
      </div>
    );
  }

  async function handleDelete() {
    const { ok, reason } = await prompt({
      title: "Удалить ВМ",
      message: `Удалить ВМ ${vm.name}? Домен и диски будут снесены. Действие необратимо.`,
      reason: true,
      reasonLabel: "Причина удаления",
      reasonRequired: true,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = mock
        ? fakeDispatch()
        : await deleteVm(vm.id, { reason: reason.trim() });
      toast.success(
        `Удаление ВМ ${vm.name} — задача поставлена (${res.task_id})`,
      );
      onDeleted?.();
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Удаление не удалось"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="p-5 flex flex-col gap-4">
      <PrepareMgmtCard
        vm={vm}
        mock={mock}
        onApplied={(next) => onEntityUpdated?.(next)}
        onChanged={onChanged}
      />

      <AstraUpdateVmCard vm={vm} mock={mock} onChanged={onChanged} />

      <VmNetworkCard vm={vm} mock={mock} onChanged={onChanged} />

      <DangerZoneCard
        buttonLabel={busy ? "Удаляем…" : "Удалить ВМ"}
        busy={busy}
        onDelete={handleDelete}
        description="Удаление ВМ сносит домен libvirt и все её диски. Действие необратимо."
      />
    </div>
  );
}

/**
 * Обновление ОС ВМ (astra-update) — аналог серверной `AstraUpdateCard`. Дропдаун
 * версий из каталога; целевая версия уходит как `rc`. Worker перезапишет
 * репозитории, прогонит `astra-update` и переснимет снимок под новую версию.
 * Доступно только для подготовленной ВМ (`is_managed`).
 */
function AstraUpdateVmCard({
  vm,
  mock,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const outcome = useTaskOutcome();
  const [pending, setPending] = useState(false);
  const [selected, setSelected] = useState("");

  const managed = vm.is_managed === true;
  const q = useQuery<{ id: string; name: string }[]>(
    async () => {
      if (mock) return MOCK_VM_OS_VERSIONS;
      const res = await listOsVersions({ limit: 200 });
      return res.items.map((v: OsVersion) => ({ id: v.id, name: v.name }));
    },
    [mock],
    { keepPreviousDataOnError: true },
  );
  const versions = q.data ?? [];
  const disabled = !managed || pending;

  async function handleUpdate() {
    const label = versions.find((v) => v.id === selected)?.name ?? selected;
    if (!label) return;
    const ok = await confirm({
      title: "Обновить ОС ВМ",
      message: `Обновить ОС ВМ ${vm.name} до ${label}? Репозитории будут перезаписаны, пойдёт astra-update, снимок переснимется под новую версию.`,
      confirmLabel: "Обновить",
      danger: true,
    });
    if (!ok) return;
    outcome.reset();
    setPending(true);
    try {
      const res = mock ? fakeDispatch() : await astraUpdateVm(vm.id, { rc: label });
      outcome.track(`astra-update · ${label}`, res.task_id, res.status);
      toast.success(`Обновление ОС ВМ ${vm.name} — задача поставлена`);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Обновление ОС не удалось"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <ArrowUpCircle className="w-4 h-4 text-accent" /> Обновление ОС Astra
      </h3>
      {!managed ? (
        <div className="text-[11px] text-dim italic">
          Обновление идёт по управляющему ключу — сначала подготовьте ВМ.
        </div>
      ) : (
        <>
          <div className="text-xs text-dim mb-3">
            Обновляет ВМ до выбранной версии ОС из каталога: worker откатится на
            нужный <span className="mono">_build</span>-снимок, перезапишет
            репозитории, выполнит{" "}
            <span className="mono">astra-update</span> и переснимет снимок.
          </div>
          <div className="flex items-end gap-2 flex-wrap">
            <label className="flex flex-col gap-1 text-sm flex-1 min-w-[200px]">
              <span className="text-dim text-xs">Целевая версия ОС</span>
              {q.loading ? (
                <div className="text-xs text-dim">Загрузка каталога…</div>
              ) : (
                <select
                  className="input"
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                  disabled={disabled}
                >
                  <option value="">— выберите версию —</option>
                  {versions.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              )}
            </label>
            <button
              className="btn btn-danger flex items-center gap-1"
              disabled={disabled || !selected}
              onClick={handleUpdate}
            >
              <ArrowUpCircle className="w-4 h-4" />
              {pending ? "Запускаем…" : "Обновить ОС"}
            </button>
          </div>
        </>
      )}
      {outcome.tracked && (
        <TaskOutcomeBanner
          outcome={outcome.tracked}
          className="mt-3"
          successText="Обновление ОС применено."
          onCancelled={outcome.reset}
        />
      )}
    </div>
  );
}

/**
 * Смена сети ВМ (`POST /vms/{id}/network`). Переключает домен bridge ↔ NAT;
 * для bridge даёт выбрать пул IPAM и адрес (свободный автоматически либо
 * вручную). Backend правит XML, для статики прописывает адрес в госте и
 * ребутит — поэтому 202-задача.
 */
function VmNetworkCard({
  vm,
  mock,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const outcome = useTaskOutcome();
  const [pending, setPending] = useState(false);
  const [mode, setMode] = useState<VmNetworkMode>(vm.network_mode);
  const [poolId, setPoolId] = useState("");
  const [ipMode, setIpMode] = useState<"auto" | "pool" | "manual">("auto");
  const [ip, setIp] = useState("");

  const poolsQ = useQuery<VmIpPool[]>(
    async () => {
      if (mock) return MOCK_VM_IP_POOLS;
      const res = await listVmIpPools({ department_id: vm.department_id });
      return res.items;
    },
    [mock, vm.department_id],
    { enabled: mode === "bridge", keepPreviousDataOnError: true },
  );
  const pools = (poolsQ.data ?? []).filter(
    (p) => !p.server_id || p.server_id === vm.hub_server_id,
  );

  const ipsQ = useQuery<string[]>(
    async () => {
      if (!poolId) return [];
      if (mock) return MOCK_AVAILABLE_IPS[poolId] ?? [];
      const res = await getAvailableIps(poolId);
      return res.ips;
    },
    [mock, poolId],
    { enabled: !!poolId && ipMode === "pool", keepPreviousDataOnError: true },
  );
  const availableIps = ipsQ.data ?? [];

  async function handleApply() {
    const ipAddress = mode === "bridge" && ipMode !== "auto" ? ip.trim() || null : null;
    const pool = mode === "bridge" ? poolId || null : null;
    const ok = await confirm({
      title: "Сменить сеть ВМ",
      message: `Переложить сеть ВМ ${vm.name} на «${mode}»? Домен будет перенастроен; для статики адрес пропишется в госте с ребутом.`,
      confirmLabel: "Сменить",
      danger: true,
    });
    if (!ok) return;
    outcome.reset();
    setPending(true);
    try {
      const res = mock
        ? fakeDispatch()
        : await setVmNetwork(vm.id, {
            network_mode: mode,
            ip_address: ipAddress,
            pool_id: pool,
          });
      outcome.track(`network · ${vm.name}`, res.task_id, res.status);
      toast.success(`Смена сети ВМ ${vm.name} — задача поставлена`);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Смена сети не удалась"));
    } finally {
      setPending(false);
    }
  }

  const changed =
    mode !== vm.network_mode ||
    (mode === "bridge" && (poolId !== "" || (ipMode !== "auto" && ip.trim() !== "")));

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <Network className="w-4 h-4 text-accent" /> Смена сети
      </h3>
      <div className="text-xs text-dim mb-3">
        Текущий режим: <span className="mono">{vm.network_mode}</span>
        {vm.ip_address ? (
          <>
            {" · "}
            <span className="mono">{vm.ip_address}</span>
          </>
        ) : null}
        . Bridge вешает ВМ на мост хаба со статикой из пула; NAT — на libvirt-сеть.
      </div>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Сетевой режим</span>
          <select
            className="input"
            value={mode}
            onChange={(e) => setMode(e.target.value as VmNetworkMode)}
            disabled={pending}
          >
            <option value="bridge">bridge (мост, статика)</option>
            <option value="nat">nat (libvirt NAT)</option>
          </select>
        </label>

        {mode === "bridge" && (
          <>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">Пул IPAM</span>
              <select
                className="input"
                value={poolId}
                onChange={(e) => {
                  setPoolId(e.target.value);
                  setIp("");
                }}
                disabled={pending}
              >
                <option value="">— авто-выбор пула —</option>
                {pools.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {p.cidr}
                    {p.server_id ? " · хаб-override" : ""}
                  </option>
                ))}
              </select>
            </label>

            <fieldset className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">IP-адрес</span>
              <div className="flex items-center gap-3 flex-wrap">
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    checked={ipMode === "auto"}
                    onChange={() => setIpMode("auto")}
                  />
                  свободный автоматически
                </label>
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    checked={ipMode === "pool"}
                    onChange={() => setIpMode("pool")}
                    disabled={!poolId}
                  />
                  выбрать из пула
                </label>
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    checked={ipMode === "manual"}
                    onChange={() => setIpMode("manual")}
                  />
                  вручную
                </label>
              </div>
              {ipMode === "pool" &&
                (ipsQ.loading ? (
                  <div className="text-xs text-dim mt-1">Загрузка свободных IP…</div>
                ) : availableIps.length === 0 ? (
                  <div className="text-xs text-warn mt-1">
                    В пуле нет свободных адресов.
                  </div>
                ) : (
                  <select
                    className="input mt-1"
                    value={ip}
                    onChange={(e) => setIp(e.target.value)}
                  >
                    <option value="">— выберите адрес —</option>
                    {availableIps.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                ))}
              {ipMode === "manual" && (
                <input
                  className="input mt-1"
                  value={ip}
                  onChange={(e) => setIp(e.target.value)}
                  placeholder="10.177.103.51"
                />
              )}
            </fieldset>
          </>
        )}

        <div className="flex justify-end">
          <button
            className="btn btn-primary flex items-center gap-1"
            disabled={pending || !changed}
            onClick={handleApply}
          >
            <Network className="w-4 h-4" />
            {pending ? "Запускаем…" : "Сменить сеть"}
          </button>
        </div>
      </div>
      {outcome.tracked && (
        <TaskOutcomeBanner
          outcome={outcome.tracked}
          className="mt-3"
          successText="Сеть переключена."
          onCancelled={outcome.reset}
        />
      )}
    </div>
  );
}

function PrepareMgmtCard({
  vm,
  mock,
  onApplied,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  onApplied: (next: Vm) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const outcome = useTaskOutcome();
  const [pending, setPending] = useState(false);
  const [fingerprint, setFingerprint] = useState<string | null>(null);

  const managed = vm.is_managed === true;
  // pending — либо backend ещё применяет ротацию, либо мы поллим задачу.
  const applying =
    vm.mgmt_creds_pending_apply === true || (outcome.tracked?.polling ?? false);

  const pubKey = vm.mgmt_ssh_public_key ?? null;
  useEffect(() => {
    let alive = true;
    sshKeyFingerprint(pubKey).then((fp) => {
      if (alive) setFingerprint(fp);
    });
    return () => {
      alive = false;
    };
  }, [pubKey]);

  async function handlePrepare() {
    const ok = await confirm({
      title: "Подготовить ВМ",
      message: `Подготовить ВМ ${vm.name}? Worker зайдёт по базовой учётке u:1, выполнит bootstrap, снесёт базовую учётку и заведёт управляющие креды.`,
      confirmLabel: "Подготовить",
    });
    if (!ok) return;
    outcome.reset();
    setPending(true);
    try {
      const res = mock ? fakeDispatch() : await prepareVm(vm.id);
      outcome.track(`prepare · ${vm.name}`, res.task_id, res.status);
      toast.success(`Подготовка ВМ ${vm.name} — задача поставлена`);
      if (mock) {
        onApplied({
          ...vm,
          is_managed: true,
          mgmt_user: "dbosmgr",
          mgmt_creds_rotated_at: new Date().toISOString(),
          mgmt_creds_pending_apply: false,
        });
      }
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Подготовка ВМ не удалась"));
    } finally {
      setPending(false);
    }
  }

  async function handleRotate() {
    const ok = await confirm({
      title: "Ротировать управляющие креды",
      message: `Сгенерировать новые управляющие креды ВМ ${vm.name} и применить их через worker? Старый материал будет отозван.`,
      confirmLabel: "Ротировать",
      danger: true,
    });
    if (!ok) return;
    outcome.reset();
    setPending(true);
    try {
      const res = mock ? fakeDispatch() : await rotateVmMgmtCreds(vm.id);
      outcome.track(`mgmt rotate · ${vm.name}`, res.task_id, res.status);
      toast.success(`Ротация кред ВМ ${vm.name} — задача поставлена`);
      if (mock) {
        onApplied({ ...vm, mgmt_creds_pending_apply: true });
      }
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Ротация кред не удалась"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-accent" /> Подготовка и управляющие
        креды
        {applying && (
          <span className="badge badge-warn text-[11px]">
            ротация применяется…
          </span>
        )}
      </h3>

      {!managed ? (
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex-1 text-xs text-dim">
            ВМ ещё не подготовлена: базовая учётка <span className="mono">u:1</span>{" "}
            не снята, управляющих кред нет. Подготовка заведёт per-VM креды и
            уберёт базовый доступ.
          </div>
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={handlePrepare}
            disabled={pending}
          >
            <ShieldCheck className="w-4 h-4" />
            {pending ? "Ставим задачу…" : "Подготовить"}
          </button>
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1.5 text-sm mb-3">
            <Field k="Состояние" v="подготовлена" />
            <Field k="mgmt-учётка" v={vm.mgmt_user ?? "—"} mono />
            <Field k="fingerprint" v={fingerprint ?? "—"} mono />
            <Field
              k="Креды ротированы"
              v={
                vm.mgmt_creds_rotated_at
                  ? formatMskShort(vm.mgmt_creds_rotated_at)
                  : "—"
              }
              mono
            />
          </dl>
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 text-xs text-dim">
              Ротация генерирует новую управляющую пару/пароль ВМ и применяет их
              через worker.
            </div>
            <button
              className="btn btn-danger flex items-center gap-1"
              onClick={handleRotate}
              disabled={pending || applying}
              title="Ротировать управляющие креды ВМ"
            >
              <KeyRound className="w-4 h-4" />
              {applying ? "Ротация идёт…" : "Ротировать креды"}
            </button>
          </div>
        </>
      )}

      {outcome.tracked && (
        <TaskOutcomeBanner
          outcome={outcome.tracked}
          className="mt-3"
          successText="Операция применена."
          onCancelled={outcome.reset}
        />
      )}
    </div>
  );
}


