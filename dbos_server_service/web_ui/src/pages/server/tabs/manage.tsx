/**
 * Manage-вкладка карточки сервера и ВМ — единый компонент.
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
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
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
  Network,
  Gauge,
  type LucideIcon,
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
  installNodeExporter,
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
  inventorySyncVm,
  installNodeExporterVm,
  listVmIpPools,
  prepareVm,
  usersInventoryVm,
  prepareVmsHub,
  releaseVm,
  reserveVm,
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
  /** Перечитать родителя после завершения async worker-задачи. */
  onChanged?: () => void;
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

/** Опция каталога версий ОС для карточки обновления Astra. */
interface AstraVersionOption {
  id: string;
  name: string;
  /** Доп. пометка после имени (например, «(нет репозиториев)»). */
  hint?: string;
}

export function ManageTab(props: Props) {
  const { entity, onServerUpdated, onDeleted, onEntityUpdated, onChanged } = props;
  const vmEntity = entity?.kind === "vm" ? entity : null;
  const isVm = !!vmEntity;
  const vm = vmEntity?.vm;

  const { persona } = usePersona();
  const toast = useToast();
  const { confirm, prompt } = useConfirm();
  const [busy, setBusyLocal] = useState<string | null>(null);

  // Трекеры исхода задач под свои карточки: lifecycle (server prepare/inventory/
  // users либо vm.prepare), ротация кред, обновление ОС, подготовка VMS-hub.
  const taskOutcome = useTaskOutcome();
  const rotateOutcome = useTaskOutcome();
  const astraOutcome = useTaskOutcome();
  const vmsHubOutcome = useTaskOutcome();
  const rotateHandledRef = useRef<string | null>(null);
  const astraHandledRef = useRef<string | null>(null);
  const vmsHubHandledRef = useRef<string | null>(null);
  const taskHandledRef = useRef<string | null>(null);

  // Серверная ветка держит локальную копию Server: lifecycle/busy-мутации
  // возвращают свежий объект — правим копию и поднимаем наверх, чтобы header и
  // соседние вкладки увидели новый busy/status без перезагрузки.
  const [current, setCurrent] = useState<Server | undefined>(props.server);
  useEffect(() => {
    setCurrent(props.server);
  }, [props.server]);
  const serverView = current ?? props.server;

  const reserverLabel = useUserLabel(
    isVm ? vm?.busy_user_id ?? null : serverView?.busy_user_id ?? null,
  );

  const applyServer = useCallback(
    (next: Server) => {
      setCurrent(next);
      onServerUpdated?.(next);
    },
    [onServerUpdated],
  );

  const refreshServer = useCallback(() => {
    if (!serverView) return;
    getServer(serverView.id)
      .then((next) => {
        applyServer(next);
        onChanged?.();
      })
      .catch(() => {});
  }, [serverView, applyServer, onChanged]);

  // Refetch по succeeded: сервер перечитывает карточку, ВМ перечитывает список
  // родителя. Ref'ы гасят повторный refetch на re-render'ах.
  useEffect(() => {
    const t = taskOutcome.tracked;
    if (!t || t.polling || t.status !== "succeeded") return;
    if (taskHandledRef.current === t.taskId) return;
    taskHandledRef.current = t.taskId;
    if (isVm) vmEntity?.onChanged();
    else refreshServer();
  }, [isVm, taskOutcome.tracked, vmEntity, refreshServer]);

  useEffect(() => {
    const t = rotateOutcome.tracked;
    if (!t || t.polling || t.status !== "succeeded") return;
    if (rotateHandledRef.current === t.taskId) return;
    rotateHandledRef.current = t.taskId;
    if (isVm) vmEntity?.onChanged();
    else refreshServer();
  }, [isVm, rotateOutcome.tracked, vmEntity, refreshServer]);

  useEffect(() => {
    const t = astraOutcome.tracked;
    if (!t || t.polling || t.status !== "succeeded") return;
    if (astraHandledRef.current === t.taskId) return;
    astraHandledRef.current = t.taskId;
    if (isVm) vmEntity?.onChanged();
    else refreshServer();
  }, [isVm, astraOutcome.tracked, vmEntity, refreshServer]);

  useEffect(() => {
    const t = vmsHubOutcome.tracked;
    if (!t || t.polling || t.status !== "succeeded") return;
    if (vmsHubHandledRef.current === t.taskId) return;
    vmsHubHandledRef.current = t.taskId;
    if (isVm) vmEntity?.onChanged();
    else refreshServer();
  }, [isVm, vmsHubOutcome.tracked, vmEntity, refreshServer]);

  // Каталог версий ОС для карточки обновления — общий для сервера и ВМ. В
  // mock-режиме ВМ берём фикстуру, иначе — реальный каталог os_versions.
  const versionsQ = useQuery<AstraVersionOption[]>(
    async () => {
      if (vmEntity?.mock) {
        return MOCK_VM_OS_VERSIONS.map((v) => ({ id: v.id, name: v.name }));
      }
      const res = await listOsVersions({ limit: 200 });
      return res.items.map((v) => ({
        id: v.id,
        name: v.name,
        hint: v.repositories.length === 0 ? " (нет репозиториев)" : "",
      }));
    },
    [vmEntity?.mock],
    { keepPreviousDataOnError: true },
  );
  const versions = useMemo(() => versionsQ.data ?? [], [versionsQ.data]);

  // Аккаунты сервера — нужны inventory/users SSH-задачам на неуправляемом
  // сервере. Для ВМ не тянем (у ВМ свой пул на вкладке «Аккаунты»).
  const accountsQ = useQuery(
    () => listAccounts({ server_id: serverView!.id, limit: 200 }),
    [serverView?.id],
    { enabled: !isVm && !!serverView },
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

  // ── Ветка ВМ ──────────────────────────────────────────────────────────────
  if (vmEntity && vm) {
    const { mock, canManage, onChanged } = vmEntity;
    if (!canManage) {
      return (
        <div className="p-5">
          <div className="text-[11px] text-dim italic">
            Нет прав на управление этой ВМ.
          </div>
        </div>
      );
    }

    const managed = vm.is_managed === true;
    // Бронь считаем из booking-статуса: `free`/`error` — свободна, иначе занята
    // (run test / debug test / логин). busy_state — индикатор идущей
    // lifecycle-операции, а не брони, поэтому в этот расчёт не входит.
    const reserved = vm.status !== "free" && vm.status !== "error";
    const foreign = !!vm.busy_user_id && vm.busy_user_id !== persona.id;

    const handleVmPrepare = async () => {
      if (
        !(await confirm({
          title: "Подготовить ВМ",
          message: `Подготовить ВМ ${vm.name}? Worker зайдёт по базовой учётке u:1, выполнит bootstrap, снесёт базовую учётку и заведёт управляющие креды.`,
          confirmLabel: "Подготовить",
        }))
      )
        return;
      taskOutcome.reset();
      setBusyLocal("prepare");
      try {
        const res = mock ? fakeDispatch() : await prepareVm(vm.id);
        taskOutcome.track(`prepare · ${vm.name}`, res.task_id, res.status);
        toast.success(`Подготовка ВМ ${vm.name} — задача поставлена`);
        if (mock) {
          onEntityUpdated?.({
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
        setBusyLocal(null);
      }
    };

    const handleVmInventory = async () => {
      taskOutcome.reset();
      const res = await run("inventory_sync", () =>
        mock ? Promise.resolve(fakeDispatch()) : inventorySyncVm(vm.id),
      );
      if (res) {
        taskOutcome.track("inventory_sync", res.task_id, res.status);
        onChanged();
      }
    };

    const handleVmUsers = async () => {
      taskOutcome.reset();
      const res = await run("users_inventory", () =>
        mock ? Promise.resolve(fakeDispatch()) : usersInventoryVm(vm.id),
      );
      if (res) {
        taskOutcome.track("users_inventory", res.task_id, res.status);
        onChanged();
      }
    };

    const handleVmNodeExporter = async () => {
      if (
        !(await confirm({
          title: "Установить node_exporter",
          message: `Установить node_exporter на ВМ ${vm.name}? Worker зайдёт в гостя через hub и выполнит установку под sudo.`,
          confirmLabel: "Установить",
        }))
      )
        return;
      taskOutcome.reset();
      const res = await run("node_exporter", () =>
        mock ? Promise.resolve(fakeDispatch()) : installNodeExporterVm(vm.id),
      );
      if (res) {
        taskOutcome.track("node_exporter", res.task_id, res.status);
        onChanged();
      }
    };

    const handleVmAstra = async (osVersionId: string) => {
      const label =
        versions.find((v) => v.id === osVersionId)?.name ?? osVersionId;
      if (!label) return;
      if (
        !(await confirm({
          title: "Обновить ОС ВМ",
          message: `Обновить ОС ВМ ${vm.name} до ${label}? Репозитории будут перезаписаны, пойдёт astra-update, снимок переснимется под новую версию.`,
          confirmLabel: "Обновить",
          danger: true,
        }))
      )
        return;
      astraOutcome.reset();
      setBusyLocal("astra_update");
      try {
        const res = mock
          ? fakeDispatch()
          : await astraUpdateVm(vm.id, { rc: label });
        astraOutcome.track(`astra-update · ${label}`, res.task_id, res.status);
        toast.success(`Обновление ОС ВМ ${vm.name} — задача поставлена`);
        onChanged();
      } catch (e) {
        toast.error(apiErrMsg(e, "Обновление ОС не удалось"));
      } finally {
        setBusyLocal(null);
      }
    };

    const handleVmRotate = async () => {
      if (
        !(await confirm({
          title: "Ротировать управляющие креды",
          message: `Сгенерировать новые управляющие креды ВМ ${vm.name} и применить их через worker? Старый материал будет отозван.`,
          confirmLabel: "Ротировать",
          danger: true,
        }))
      )
        return;
      rotateOutcome.reset();
      setBusyLocal("mgmt_rotate");
      try {
        const res = mock ? fakeDispatch() : await rotateVmMgmtCreds(vm.id);
        rotateOutcome.track(`mgmt rotate · ${vm.name}`, res.task_id, res.status);
        toast.success(`Ротация кред ВМ ${vm.name} — задача поставлена`);
        if (mock) onEntityUpdated?.({ ...vm, mgmt_creds_pending_apply: true });
        onChanged();
      } catch (e) {
        toast.error(apiErrMsg(e, "Ротация кред не удалась"));
      } finally {
        setBusyLocal(null);
      }
    };

    const handleVmReserve = async (reason: string) => {
      setBusyLocal("busy_set");
      try {
        const next = mock
          ? ({
              ...vm,
              busy_state: "busy",
              busy_note: reason,
              status: reason,
            } as Vm)
          : await reserveVm(vm.id, { reason });
        onEntityUpdated?.(next);
        onChanged();
        toast.success(`ВМ ${vm.name} забронирована`);
      } catch (e) {
        toast.error(apiErrMsg(e, "Не удалось забронировать"));
      } finally {
        setBusyLocal(null);
      }
    };

    const handleVmRelease = async () => {
      const message = foreign
        ? "ВМ забронирована другим пользователем. Снять бронь принудительно? После освобождения её сможет занять любой."
        : `Снять бронь с ВМ ${vm.name}?`;
      if (!(await confirm({ message }))) return;
      setBusyLocal("busy_clear");
      try {
        const next = mock
          ? ({
              ...vm,
              busy_state: "free",
              busy_note: null,
              status: "free",
            } as Vm)
          : await releaseVm(vm.id);
        onEntityUpdated?.(next);
        onChanged();
        toast.success(`Бронь с ВМ ${vm.name} снята`);
      } catch (e) {
        toast.error(apiErrMsg(e, "Не удалось снять бронь"));
      } finally {
        setBusyLocal(null);
      }
    };

    const handleVmDelete = async () => {
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
      setBusyLocal("delete");
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
        setBusyLocal(null);
      }
    };

    return (
      <div className="p-5 flex flex-col gap-4">
        <LifecycleCard
          ready
          prepared={managed}
          allowed={canManage}
          busyLabel={busy}
          infoRows={
            managed
              ? [
                  { k: "Состояние", v: "подготовлена" },
                  { k: "mgmt-учётка", v: vm.mgmt_user ?? "—", mono: true },
                ]
              : undefined
          }
          description={
            managed ? (
              "ВМ подготовлена: заведены per-VM управляющие креды, базовый доступ снят. Повторная подготовка перезапустит bootstrap-цикл."
            ) : (
              <>
                ВМ ещё не подготовлена: базовая учётка{" "}
                <span className="mono">u:1</span> не снята, управляющих кред нет.
                Подготовка заведёт per-VM креды и уберёт базовый доступ.
              </>
            )
          }
          notPreparedHint="ВМ не подготовлена — инвентаризация ходит по управляющему ключу. Станет доступна после успешной подготовки."
          actions={[
            {
              key: "prepare",
              label: "Prepare",
              runningLabel: "Ставим задачу…",
              Icon: Play,
              primary: !managed,
              onClick: handleVmPrepare,
              title: managed ? "Повторно подготовить ВМ" : undefined,
            },
            {
              key: "inventory_sync",
              label: "Inventory sync",
              Icon: RefreshCw,
              spin: true,
              requiresPrepared: true,
              title: managed
                ? undefined
                : "Сначала подготовьте ВМ — инвентаризация ходит по управляющему ключу",
              onClick: handleVmInventory,
            },
            {
              key: "users_inventory",
              label: "Users inventory",
              Icon: UserCheck,
              requiresPrepared: true,
              title: managed
                ? undefined
                : "Сначала подготовьте ВМ — инвентаризация ходит по управляющему ключу",
              onClick: handleVmUsers,
            },
            {
              key: "node_exporter",
              label: "node_exporter",
              Icon: Gauge,
              requiresPrepared: true,
              title: managed
                ? "Установить node_exporter для Grafana-метрик"
                : "Сначала подготовьте ВМ — установка идёт по управляющему ключу",
              onClick: handleVmNodeExporter,
            },
          ]}
          outcome={taskOutcome.tracked}
          onCancelled={taskOutcome.reset}
          successText="Операция применена."
        />

        <AstraUpdateCard
          prepared={managed}
          allowed={canManage}
          busy={busy !== null}
          submitting={busy === "astra_update"}
          versions={versions}
          versionsLoading={versionsQ.loading}
          description={
            <>
              Обновляет ВМ до выбранной версии ОС из каталога: worker откатится
              на нужный <span className="mono">_build</span>-снимок, перезапишет
              репозитории, выполнит <span className="mono">astra-update</span> и
              переснимет снимок.
            </>
          }
          notPreparedHint="Обновление идёт по управляющему ключу — сначала подготовьте ВМ."
          onUpdate={handleVmAstra}
          outcome={astraOutcome.tracked}
          onCancelled={astraOutcome.reset}
          successText="Обновление ОС применено."
        />

        <ManagementCredsCard
          prepared={managed}
          pubKey={vm.mgmt_ssh_public_key ?? null}
          rotatedAt={vm.mgmt_creds_rotated_at}
          pendingApply={vm.mgmt_creds_pending_apply === true}
          allowed={canManage}
          busy={busy !== null}
          rotateBusy={busy === "mgmt_rotate"}
          notPreparedHint="Управляющая пара появляется после подготовки ВМ — на неподготовленной ротировать нечего."
          noPermissionHint="Нет прав на ротацию управляющих кред этой ВМ."
          outcome={rotateOutcome.tracked}
          onCancelled={rotateOutcome.reset}
          onRotate={handleVmRotate}
        />

        <VmNetworkCard vm={vm} mock={mock} onChanged={onChanged} />

        <BookingCard
          entityWord="ВМ"
          reserved={reserved}
          stateLabel={vm.busy_state ?? "—"}
          note={vm.busy_note}
          reserverLabel={
            vm.busy_user_id ? (
              <span title={vm.busy_user_id}>юзер {reserverLabel}</span>
            ) : undefined
          }
          since={vm.busy_since ? formatMskShort(vm.busy_since) : undefined}
          canManage={canManage}
          foreign={foreign}
          busy={busy !== null}
          onReserve={handleVmReserve}
          onRelease={handleVmRelease}
        />

        <DangerZoneCard
          buttonLabel={busy === "delete" ? "Удаляем…" : "Удалить ВМ"}
          busy={busy !== null}
          onDelete={handleVmDelete}
          description="Удаление ВМ сносит домен libvirt и все её диски. Действие необратимо."
        />
      </div>
    );
  }

  // ── Ветка сервера ───────────────────────────────────────────────────────
  const view = serverView;
  const allowBasic = canManageBasic(persona, view);
  const allowVmsHub = canPrepareVmsHub(persona);
  // Пока идёт обновление ОС — сервер под системной блокировкой: все управляющие
  // операции backend отобьёт 409 SERVER_UPDATING. Гейтим кнопки на клиенте.
  const updating = view?.busy_state === "updating";
  const allowOsCatalog =
    isDepAdminOfServer(persona, view) ||
    persona.service_roles.server === "admin";
  const allowDelete =
    isDepAdminOfServer(persona, view) ||
    persona.service_roles.server === "admin";
  const prepared = !!view && view.is_managed;
  const prepareHint = prepared
    ? undefined
    : "Сначала запустите prepare — инвентаризация ходит по управляющему ключу";

  return (
    <div className="p-5 flex flex-col gap-4">
      <LifecycleCard
        ready={!!view}
        prepared={prepared}
        allowed={allowBasic}
        locked={updating}
        busyLabel={busy}
        tasksLink={
          view ? `/worker?server_id=${encodeURIComponent(view.id)}` : undefined
        }
        notPreparedHint="Сервер не подготовлен (нет management-пользователя). Инвентаризация станет доступна после успешного prepare."
        noPermissionHint="Нет прав на lifecycle-операции (нужна роль server.operator+ или dep_admin своего департамента)."
        actions={[
          {
            key: "prepare",
            label: "Prepare",
            runningLabel: "Запускаем…",
            Icon: Play,
            primary: true,
            title: allowBasic
              ? "Bootstrap management-цикла"
              : "Нет прав на prepare",
            onClick: async () => {
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
            },
          },
          {
            key: "inventory_sync",
            label: "Inventory sync",
            Icon: RefreshCw,
            spin: true,
            requiresPrepared: true,
            title: prepareHint,
            onClick: async () => {
              if (!view) return;
              taskOutcome.reset();
              const res = await run("inventory_sync", () =>
                inventorySync(view.id),
              );
              if (res)
                taskOutcome.track("inventory_sync", res.task_id, res.status);
            },
          },
          {
            key: "os_sync",
            label: "OS sync",
            Icon: RefreshCw,
            spin: true,
            onClick: () => {
              if (!view) return;
              setOsSyncOpen(true);
            },
          },
          {
            key: "users_inventory",
            label: "Users inventory",
            Icon: UserCheck,
            requiresPrepared: true,
            title: prepareHint,
            onClick: async () => {
              if (!view) return;
              taskOutcome.reset();
              const res = await run("users_inventory", () =>
                usersInventory(view.id),
              );
              if (res)
                taskOutcome.track("users_inventory", res.task_id, res.status);
            },
          },
          {
            key: "node_exporter",
            label: "node_exporter",
            Icon: Gauge,
            requiresPrepared: true,
            title: prepared
              ? "Установить node_exporter для Grafana-метрик"
              : prepareHint,
            onClick: async () => {
              if (!view) return;
              if (
                !(await confirm({
                  title: "Установить node_exporter",
                  message: `Установить node_exporter на ${view.hostname}? Worker зайдёт на сервер по управляющему ключу и выполнит установку под sudo.`,
                  confirmLabel: "Установить",
                }))
              )
                return;
              taskOutcome.reset();
              const res = await run("node_exporter", () =>
                installNodeExporter(view.id),
              );
              if (res)
                taskOutcome.track("node_exporter", res.task_id, res.status);
            },
          },
        ]}
        outcome={taskOutcome.tracked}
        onCancelled={taskOutcome.reset}
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
        prepared={prepared}
        allowed={allowBasic}
        locked={updating}
        busy={busy !== null}
        submitting={busy === "astra_update"}
        versions={versions}
        versionsLoading={versionsQ.loading}
        description={
          <>
            Обновляет сервер до выбранной версии ОС из каталога: полностью
            перезаписывает <span className="mono">/etc/apt/sources.list</span>{" "}
            репозиториями версии и выполняет{" "}
            <span className="mono">apt update &amp;&amp; astra-update</span>. На
            время обновления любые операции с сервером блокируются.
          </>
        }
        notPreparedHint="Обновление идёт по управляющему ключу — сначала выполните prepare."
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
        outcome={astraOutcome.tracked}
        onCancelled={astraOutcome.reset}
      />

      <ManagementCredsCard
        prepared={!!view && view.is_managed}
        pubKey={view?.mgmt_ssh_public_key ?? null}
        rotatedAt={view?.mgmt_creds_rotated_at}
        pendingApply={!!view?.mgmt_creds_pending_apply}
        allowed={allowBasic}
        locked={updating}
        busy={busy !== null}
        rotateBusy={busy === "mgmt_rotate"}
        notPreparedHint="Управляющая пара появляется после prepare — на неподготовленном сервере ротировать нечего."
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

interface LifecycleAction {
  /** Ключ операции; совпадает с меткой busy, пока идёт именно она. */
  key: string;
  label: string;
  /** Подпись, пока идёт именно это действие (иначе — label). */
  runningLabel?: string;
  Icon: LucideIcon;
  /** Крутить иконку, пока действие выполняется. */
  spin?: boolean;
  /** Акцентная кнопка (btn-primary). */
  primary?: boolean;
  /** Доступно только после подготовки (inventory-семейство у сервера). */
  requiresPrepared?: boolean;
  onClick: () => void | Promise<void>;
  title?: string;
}

/** Строка сводки в шапке карточки (у ВМ — состояние + mgmt-учётка). */
interface LifecycleInfoRow {
  k: string;
  v: ReactNode;
  mono?: boolean;
}

/**
 * Карточка «Жизненный цикл» — общая для сервера и ВМ. Заголовок, иконка и
 * раскладка серверные; различия сущностей приходят пропсами: набор кнопок
 * (`actions`), ссылка на задачи (`tasksLink`), сводка в шапке (`infoRows`) и
 * поясняющий текст (`description`). Сервер отдаёт prepare/inventory/os-sync/
 * users, у ВМ применима только подготовка.
 */
function LifecycleCard({
  ready,
  prepared,
  allowed,
  locked = false,
  busyLabel,
  actions,
  infoRows,
  description,
  tasksLink,
  notPreparedHint,
  noPermissionHint,
  outcome,
  onCancelled,
  successText,
}: {
  /** Сущность загружена (для сервера — есть объект). */
  ready: boolean;
  /** Сущность подготовлена (is_managed). */
  prepared: boolean;
  allowed: boolean;
  /** Под системной блокировкой обновления ОС — операции заблокированы. */
  locked?: boolean;
  busyLabel: string | null;
  actions: LifecycleAction[];
  infoRows?: LifecycleInfoRow[];
  description?: ReactNode;
  tasksLink?: string;
  notPreparedHint?: ReactNode;
  noPermissionHint?: ReactNode;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  successText?: string;
}) {
  const base = !allowed || locked || busyLabel !== null || !ready;
  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <h3 className="font-semibold text-base flex items-center gap-2">
          <Settings className="w-4 h-4 text-accent" /> Жизненный цикл
        </h3>
        {tasksLink && (
          <Link
            to={tasksLink}
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
      {notPreparedHint && ready && !prepared && (
        <div className="text-[11px] text-dim italic mb-3">{notPreparedHint}</div>
      )}
      {infoRows && infoRows.length > 0 && (
        <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1.5 text-sm mb-3">
          {infoRows.map((r) => (
            <Fragment key={r.k}>
              <dt className="text-dim text-xs">{r.k}</dt>
              <dd className={r.mono ? "mono" : undefined}>{r.v}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {description && <div className="text-xs text-dim mb-3">{description}</div>}
      <div className="flex gap-2 flex-wrap">
        {actions.map((a) => {
          const disabled = base || (!!a.requiresPrepared && !prepared);
          return (
            <button
              key={a.key}
              className={`btn ${a.primary ? "btn-primary " : ""}flex items-center gap-1`}
              disabled={disabled}
              onClick={a.onClick}
              title={a.title}
            >
              <a.Icon
                className={`w-4 h-4 ${a.spin && busyLabel === a.key ? "animate-spin" : ""}`}
              />
              {busyLabel === a.key ? a.runningLabel ?? a.label : a.label}
            </button>
          );
        })}
      </div>
      {noPermissionHint && !allowed && (
        <div className="text-[11px] text-dim italic mt-3">{noPermissionHint}</div>
      )}
      {outcome && (
        <TaskOutcomeBanner
          outcome={outcome}
          className="mt-3"
          successText={successText}
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
  // Карточка видна всегда, пока есть объект сервера. Готовность к подготовке
  // hub'а зависит от prepare сервера и детекта виртуализации — по ним считаем
  // причину блокировки кнопки (null = кнопка активна).
  if (!server) return null;
  const isHub = !!server.is_vms_hub;
  const disabled = !allowed || locked || busyLabel !== null;

  const virt = server.virtualization;
  let prepareBlockReason: string | null = null;
  if (!server.is_managed) {
    prepareBlockReason = "Сначала нужен prepare сервера";
  } else if (virt === false) {
    prepareBlockReason =
      "Нет аппаратной виртуализации (KVM) — сервер нельзя сделать VMS-hub";
  } else if (virt == null) {
    prepareBlockReason = "Идёт проверка виртуализации…";
  }
  const prepareDisabled = disabled || prepareBlockReason !== null;

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
            {prepareBlockReason ??
              "Сервер поддерживает виртуализацию (KVM). Подготовка развернёт libvirt/kvm, мост br0 и storage-pool и скачает образы каталога — после этого на нём можно создавать ВМ."}
          </div>
          {allowed && (
            <button
              className="btn btn-primary flex items-center gap-1"
              disabled={prepareDisabled}
              onClick={onPrepare}
              title={prepareBlockReason ?? "Подготовить сервер как VMS-hub"}
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

/**
 * Карточка «Управляющие креды» — общая для сервера и ВМ. Показывает fingerprint
 * управляющего ключа и время последней ротации, даёт кнопку ротации. Данные
 * приходят готовыми пропсами, чтобы обе сущности делили одну вёрстку.
 */
function ManagementCredsCard({
  prepared,
  pubKey,
  rotatedAt,
  pendingApply = false,
  allowed,
  locked = false,
  busy,
  rotateBusy = false,
  outcome,
  onCancelled,
  onRotate,
  notPreparedHint,
  noPermissionHint,
}: {
  /** Сущность подготовлена (есть управляющая пара). */
  prepared: boolean;
  /** Публичный SSH-ключ управляющей учётки (для fingerprint). null — нет. */
  pubKey: string | null;
  /** Момент последней ротации (ISO). null/undefined — не ротировались. */
  rotatedAt?: string | null;
  /** Backend ещё применяет свежую ротацию. */
  pendingApply?: boolean;
  allowed: boolean;
  /** Сущность под системной блокировкой обновления ОС — ротация недоступна. */
  locked?: boolean;
  /** Любая операция в процессе — блокируем кнопку. */
  busy: boolean;
  /** Именно ротация только что задиспатчена — подпись «Запускаем…». */
  rotateBusy?: boolean;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  onRotate: () => Promise<void>;
  notPreparedHint?: ReactNode;
  noPermissionHint?: ReactNode;
}) {
  // pending — либо backend ещё применяет ротацию, либо мы поллим задачу.
  const pending = pendingApply || (outcome?.polling ?? false);
  const disabled = !allowed || locked || busy || !prepared || pending;

  const [fingerprint, setFingerprint] = useState<string | null>(null);
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
          {notPreparedHint ??
            "Управляющая пара появляется после prepare — на неподготовленном ресурсе ротировать нечего."}
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 text-xs mb-3">
            <dt className="text-dim">fingerprint</dt>
            <dd className="mono break-all">
              {fingerprint ? fingerprint : <span className="text-dim">—</span>}
            </dd>
            <dt className="text-dim">rotated_at</dt>
            <dd className="mono">
              {rotatedAt ? (
                formatMskShort(rotatedAt)
              ) : (
                <span className="text-dim">—</span>
              )}
            </dd>
          </dl>

          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 text-xs text-dim">
              Генерирует новую управляющую SSH-пару и пароль, применяет их через
              worker и отзывает старый материал.
            </div>
            {allowed && (
              <button
                className="btn btn-danger flex items-center gap-1"
                disabled={disabled}
                onClick={onRotate}
                title="Ротировать управляющую пару и пароль"
              >
                <KeyRound className="w-4 h-4" />
                {pending
                  ? "Ротация идёт…"
                  : rotateBusy
                    ? "Запускаем…"
                    : "Ротировать управляющие креды"}
              </button>
            )}
          </div>
        </>
      )}

      {!allowed && (
        <div className="text-[11px] text-dim italic mt-3">
          {noPermissionHint ??
            "Нет прав на ротацию управляющих кред (нужна роль server.operator+ или dep_admin своего департамента)."}
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

/**
 * Карточка «Обновление ОС Astra» — общая для сервера и ВМ. Дропдаун версий
 * каталога и кнопка запуска одинаковы; различаются источник версий и эндпоинт
 * (server astra-update vs vm astra-update) — они приходят пропсами `versions`
 * и `onUpdate`.
 */
function AstraUpdateCard({
  prepared,
  allowed,
  locked = false,
  busy,
  submitting,
  versions,
  versionsLoading,
  description,
  notPreparedHint,
  onUpdate,
  outcome,
  onCancelled,
  successText,
}: {
  prepared: boolean;
  allowed: boolean;
  /** Сущность уже под updating-блокировкой — кнопка «идёт обновление». */
  locked?: boolean;
  /** Любая операция в процессе — блокируем контролы. */
  busy: boolean;
  /** Именно обновление ОС только что задиспатчено — подпись «Запускаем…». */
  submitting: boolean;
  versions: AstraVersionOption[];
  versionsLoading: boolean;
  description: ReactNode;
  notPreparedHint: ReactNode;
  onUpdate: (osVersionId: string) => void | Promise<void>;
  outcome: TrackedTask | null;
  onCancelled: () => void;
  successText?: string;
}) {
  const [selected, setSelected] = useState("");
  const disabled = !allowed || locked || busy || !prepared;

  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <ArrowUpCircle className="w-4 h-4 text-accent" /> Обновление ОС Astra
        {locked && (
          <span className="badge badge-warn text-[11px]">идёт обновление…</span>
        )}
      </h3>

      {!prepared ? (
        <div className="text-[11px] text-dim italic">{notPreparedHint}</div>
      ) : (
        <>
          <div className="text-xs text-dim mb-3">{description}</div>
          <div className="flex items-end gap-2 flex-wrap">
            <label className="flex flex-col gap-1 text-sm flex-1 min-w-[200px]">
              <span className="text-dim text-xs">Целевая версия ОС</span>
              {versionsLoading ? (
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
                    <option key={v.id} value={v.id} title={v.id}>
                      {v.name}
                      {v.hint ?? ""}
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
                  : submitting
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
          successText={successText}
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
// ВМ — mock-диспатч и смена сети (VM-специфичная карточка)
// ─────────────────────────────────────────────────────────────────────────────

function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
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
