/**
 * Единая рабочая зона сущности — карточка справа от Aside-списка. Эталон взят с
 * серверной панели: шапка (имя + бейджи/сигналы + мета) с блоком брони, полоса
 * вкладок и рендер активной вкладки. Сервер и ВМ используют один компонент;
 * различие — только в наборе вкладок, данных шапки и правиле брони.
 *
 * Набор вкладок считается из `kind`: общая база
 * `overview/hardware/accounts/console/packages/manage`, у сервера добавляется
 * `ipmi`, у ВМ — `power` (вместо IPMI) плюс `disks`/`snapshots`. Сами
 * файлы-вкладки общие (`@/pages/server/tabs/*`): серверу они уходят по legacy
 * пропсам `serverId`/`server`, ВМ — через `entity`.
 *
 * Локальную копию объекта (Server|Vm) держит обёртка; мутации во вкладках и
 * бронь поднимаются наверх через `onLocalUpdate`, чтобы шапка и соседние
 * вкладки обновились без перезагрузки.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Server as ServerIcon,
  MonitorPlay,
  Lock,
  Unlock,
  RefreshCw,
  Power,
  PowerOff,
} from "lucide-react";
import { Tabs } from "@/components/ui/Tabs";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { clearBusy, inventorySync, setBusy } from "@/api/server/servers";
import { osSync } from "@/api/server/osVersions";
import { getAcsAvailability } from "@/api/server/acsSnapshots";
import { useQuery } from "@/api/auth/useQuery";
import { powerOff, powerOn, powerReboot } from "@/api/server/ipmi";
import { reserveVm, releaseVm, vmBusyLabel } from "@/api/server/vms";
import { useDeptLabel, useUserLabel, useServerLabel } from "@/lib/labels";
import { isDepAdmin } from "@/lib/rbac";
import { apiErrMsg } from "@/api/client";
import { EntityHeader } from "@/components/entity/EntityHeader";
import { ReachSignal, PowerStateBadge } from "@/components/entity/signals";
import type { Server, ServerStatus, BusyState } from "@/api/server/types";
import type { Vm } from "@/api/server/vms";
import type { EntityRef } from "@/pages/server/tabs/_entity";
import { TAB_ICON } from "@/pages/server/tabs/_tabMeta";
import { OverviewTab } from "@/pages/server/tabs/overview";
import { HardwareTab } from "@/pages/server/tabs/hardware";
import { IpmiTab } from "@/pages/server/tabs/ipmi";
import { PowerTab } from "@/pages/server/tabs/power";
import { AccountsTab } from "@/pages/server/tabs/accounts";
import { ConsoleTab } from "@/pages/server/tabs/console";
import { PackagesTab } from "@/pages/server/tabs/packages";
import { MetricsTab } from "@/pages/server/tabs/metrics";
import { ManageTab } from "@/pages/server/tabs/manage";
import { DisksTab } from "@/pages/server/tabs/disks";
import { SnapshotsTab } from "@/pages/server/tabs/snapshots";
import { AcsSnapshotsTab } from "@/pages/server/tabs/acsSnapshots";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

type TabId =
  | "overview"
  | "hardware"
  | "ipmi"
  | "power"
  | "accounts"
  | "console"
  | "packages"
  | "metrics"
  | "manage"
  | "disks"
  | "snapshots"
  | "acsSnapshots";

const TAB_LABEL: Record<TabId, string> = {
  overview: "Обзор",
  hardware: "Железо",
  ipmi: "IPMI",
  power: "Питание",
  accounts: "Аккаунты",
  console: "Консоль",
  packages: "Пакеты",
  metrics: "Графики",
  manage: "Управление",
  disks: "Диски",
  snapshots: "Снимки",
  acsSnapshots: "Снимки",
};

/**
 * Набор вкладок по типу сущности. База одинакова; сервер несёт IPMI, ВМ —
 * питание домена вместо IPMI и дополнительно диски/снимки.
 *
 * `acsAvailable` прячет вкладку «Снимки ACS» целиком (не просто показывает
 * пустой блок внутри), когда функция недоступна серверу — платформенно
 * выключена, не включена для отдела сервера, либо нет права
 * `acs_snapshot_list`. Для ВМ параметр не участвует.
 */
function tabsFor(kind: EntityRef["kind"], acsAvailable: boolean): TabId[] {
  const mid: TabId = kind === "server" ? "ipmi" : "power";
  const tail: TabId[] =
    kind === "vm" ? ["disks", "snapshots"] : acsAvailable ? ["acsSnapshots"] : [];
  return [
    "overview",
    "hardware",
    mid,
    "accounts",
    "console",
    "packages",
    "metrics",
    "manage",
    ...tail,
  ];
}

const STATUS_LABEL: Record<ServerStatus, string> = {
  unknown: "Unknown",
  online: "Online",
  offline: "Offline",
  maintenance: "Maintenance",
  decommissioned: "Decommissioned",
};

const STATUS_KIND: Record<ServerStatus, "ok" | "warn" | "danger" | ""> = {
  unknown: "",
  online: "ok",
  offline: "danger",
  maintenance: "warn",
  decommissioned: "",
};

const BUSY_LABEL: Record<BusyState, string> = {
  free: "Свободен",
  busy: "Занят",
  testing: "В тесте",
  updating: "Обновление ОС",
  acs: "Снимок ACS",
};

const BUSY_KIND: Record<BusyState, "ok" | "warn" | "danger"> = {
  free: "ok",
  busy: "warn",
  testing: "warn",
  updating: "warn",
  acs: "warn",
};

interface EntityDetailProps {
  entity: EntityRef;
  /** Поднять свежий объект после мутации во вкладке или брони. */
  onLocalUpdate: (next: Server | Vm) => void;
  /** После удаления сущности (danger zone во «Управлении»). */
  onDeleted?: () => void;
  /** Сервер: обновить индикатор брони в списке слева. ВМ дёргает entity.onChanged. */
  onBusyChanged?: () => void;
  /** Перечитать родительские данные после async worker-мутаций. */
  onChanged?: () => void;
  /** ВМ: возврат к списку хаба. */
  onBack?: () => void;
  backLabel?: string;
}

export function EntityDetail({
  entity,
  onLocalUpdate,
  onDeleted,
  onBusyChanged,
  onChanged,
  onBack,
  backLabel,
}: EntityDetailProps) {
  const [tab, setTab] = useState<TabId>("overview");
  const serverId = entity.kind === "server" ? entity.server.id : null;
  // Видимость вкладки «Снимки ACS» решается ДО рендера таб-бара — фоновая
  // проверка (без audit, всегда 200), см. `getAcsAvailability`. Пока грузится
  // или запрос ещё не стартовал — вкладку не показываем (false), чтобы не
  // мигать лишней вкладкой при входе на карточку.
  const acsAvailabilityQ = useQuery(
    async () => (serverId ? (await getAcsAvailability(serverId)).available : false),
    [serverId],
    { keepPreviousDataOnError: true },
  );
  const acsAvailable = acsAvailabilityQ.data ?? false;
  const tabList = useMemo(
    () => tabsFor(entity.kind, acsAvailable),
    [entity.kind, acsAvailable],
  );
  const activeTab = tabList.includes(tab) ? tab : "overview";
  const serverOsVersionRef = useRef<string | null>(null);
  serverOsVersionRef.current =
    entity.kind === "server" ? entity.server.os_version_id ?? null : null;

  // При входе в обзор/железо запускаем лёгкий refresh инвентаризации. Зависим
  // только от id и вкладки, чтобы polling свежего server-объекта не создавал
  // бесконечную очередь sync-задач.
  useEffect(() => {
    if (!serverId) return;
    if (activeTab !== "overview" && activeTab !== "hardware") return;
    Promise.resolve(inventorySync(serverId)).catch(() => {});
    Promise.resolve(
      osSync(serverId, { os_version_id: serverOsVersionRef.current }),
    ).catch(() => {});
  }, [serverId, activeTab]);

  return (
    <section className="flex-1 min-w-0 overflow-hidden flex flex-col">
      <EntityDetailHeader
        entity={entity}
        onLocalUpdate={onLocalUpdate}
        onBusyChanged={onBusyChanged}
        onChanged={onChanged}
        onBack={onBack}
        backLabel={backLabel}
      />
      <Tabs
        active={activeTab}
        onChange={(id) => setTab(id as TabId)}
        wrap
        tabs={tabList.map((id) => ({
          id,
          label: TAB_LABEL[id],
          icon: TAB_ICON[id],
        }))}
      />
      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-auto">
        <EntityTab
          entity={entity}
          activeTab={activeTab}
          onLocalUpdate={onLocalUpdate}
          onDeleted={onDeleted}
          onChanged={onChanged}
          onBack={onBack}
        />
      </div>
    </section>
  );
}

/** Рендер активной вкладки: серверу — legacy-пропсы, ВМ — через `entity`. */
function EntityTab({
  entity,
  activeTab,
  onLocalUpdate,
  onDeleted,
  onChanged,
  onBack,
}: {
  entity: EntityRef;
  activeTab: TabId;
  onLocalUpdate: (next: Server | Vm) => void;
  onDeleted?: () => void;
  onChanged?: () => void;
  onBack?: () => void;
}) {
  if (entity.kind === "server") {
    const s = entity.server;
    switch (activeTab) {
      case "overview":
        return (
          <OverviewTab
            entity={entity}
            serverId={s.id}
            server={s}
            onServerUpdated={onLocalUpdate}
          />
        );
      case "hardware":
        return (
          <HardwareTab
            entity={entity}
            serverId={s.id}
            server={s}
            onServerUpdated={onLocalUpdate}
          />
        );
      case "ipmi":
        return <IpmiTab serverId={s.id} server={s} />;
      case "accounts":
        return <AccountsTab entity={entity} serverId={s.id} server={s} />;
      case "console":
        return <ConsoleTab entity={entity} serverId={s.id} server={s} />;
      case "packages":
        return (
          <PackagesTab
            entity={entity}
            serverId={s.id}
            server={s}
            onServerUpdated={onLocalUpdate}
          />
        );
      case "metrics":
        return <MetricsTab entity={entity} />;
      case "manage":
        return (
          <ManageTab
            entity={entity}
            serverId={s.id}
            server={s}
            onServerUpdated={onLocalUpdate}
            onDeleted={onDeleted}
            onChanged={onChanged}
          />
        );
      case "acsSnapshots":
        return <AcsSnapshotsTab serverId={s.id} server={s} />;
      default:
        return null;
    }
  }

  switch (activeTab) {
    case "overview":
      return <OverviewTab entity={entity} onEntityUpdated={onLocalUpdate} />;
    case "hardware":
      return <HardwareTab serverId="" entity={entity} />;
    case "power":
      return <PowerTab entity={entity} onEntityUpdated={onLocalUpdate} />;
    case "accounts":
      return <AccountsTab serverId="" entity={entity} />;
    case "console":
      return <ConsoleTab entity={entity} />;
    case "packages":
      return <PackagesTab serverId="" entity={entity} />;
    case "metrics":
      return <MetricsTab entity={entity} />;
    case "manage":
      return (
        <ManageTab
          serverId=""
          entity={entity}
          onEntityUpdated={onLocalUpdate}
          onDeleted={onBack}
        />
      );
    case "disks":
      return <DisksTab entity={entity} />;
    case "snapshots":
      return <SnapshotsTab entity={entity} onEntityUpdated={onLocalUpdate} />;
    default:
      return null;
  }
}

/** Шапка карточки с блоком брони, ветвящаяся по типу сущности. */
function EntityDetailHeader({
  entity,
  onLocalUpdate,
  onBusyChanged,
  onChanged,
  onBack,
  backLabel,
}: {
  entity: EntityRef;
  onLocalUpdate: (next: Server | Vm) => void;
  onBusyChanged?: () => void;
  onChanged?: () => void;
  onBack?: () => void;
  backLabel?: string;
}) {
  if (entity.kind === "server") {
    return (
      <ServerDetailHeader
        server={entity.server}
        onServerUpdated={onLocalUpdate}
        onBusyChanged={onBusyChanged}
        onChanged={onChanged}
      />
    );
  }
  return (
    <VmDetailHeader
      vm={entity.vm}
      mock={entity.mock}
      canManage={entity.canManage}
      onLocalUpdate={onLocalUpdate}
      onChanged={entity.onChanged}
      onBack={onBack}
      backLabel={backLabel}
    />
  );
}

function ServerDetailHeader({
  server,
  onServerUpdated,
  onBusyChanged,
  onChanged,
}: {
  server: Server;
  onServerUpdated: (next: Server) => void;
  onBusyChanged?: () => void;
  onChanged?: () => void;
}) {
  const deptLabel = useDeptLabel(server.department_id);
  const statusKind = STATUS_KIND[server.status];
  const busyKind = BUSY_KIND[server.busy_state];
  const name = server.display_name ?? server.hostname;
  return (
    <EntityHeader
      icon={<ServerIcon className="w-7 h-7" />}
      name={name}
      badges={
        <>
          <Badge kind={statusKind || "neutral"}>
            {STATUS_LABEL[server.status] ?? server.status}
          </Badge>
          <Badge kind={busyKind ?? "neutral"}>
            {BUSY_LABEL[server.busy_state] ?? server.busy_state}
          </Badge>
          <ReachSignal
            label="ping"
            reachable={server.ping_reachable}
            latencyMs={server.ping_latency_ms}
          />
          <ReachSignal
            label="ssh"
            reachable={server.ssh_reachable}
            latencyMs={server.ssh_latency_ms}
          />
          {/* Сигнал питания снимается по IPMI/BMC. */}
          <PowerStateBadge state={server.power_state} />
        </>
      }
      meta={
        <>
          <span className="mono">{server.id}</span>
          <span>·</span>
          <span>
            hostname: <b className="mono">{server.hostname}</b>
          </span>
          <span>·</span>
          <span>
            IP: <span className="mono">{server.ip_address}</span>
          </span>
          <span>·</span>
          <span>
            dept: <b>{deptLabel}</b>
          </span>
        </>
      }
    >
      <div className="mt-3 flex items-center gap-3 flex-wrap">
        <ServerHeaderPowerControls server={server} onChanged={onChanged} />
        <ServerReserveControl
          server={server}
          onServerUpdated={onServerUpdated}
          onBusyChanged={onBusyChanged}
        />
      </div>
    </EntityHeader>
  );
}

function VmDetailHeader({
  vm,
  mock,
  canManage,
  onLocalUpdate,
  onChanged,
  onBack,
  backLabel,
}: {
  vm: Vm;
  mock: boolean;
  canManage: boolean;
  onLocalUpdate: (next: Vm) => void;
  onChanged: () => void;
  onBack?: () => void;
  backLabel?: string;
}) {
  const deptLabel = useDeptLabel(vm.department_id);
  const hubLabel = useServerLabel(vm.hub_server_id);
  // ВМ недоступна: не выключена штатно (power on/unknown — напр. хаб недоступен),
  // но ни ping, ни ssh до гостя не отвечают. При power=off это штатное
  // «выключена» (его показывает PowerStateBadge), не дублируем как «недоступна».
  const unreachable =
    vm.power_state !== "off" &&
    vm.ping_reachable === false &&
    vm.ssh_reachable === false;
  return (
    <EntityHeader
      icon={<MonitorPlay className="w-7 h-7" />}
      onBack={onBack}
      backLabel={backLabel}
      name={vm.name}
      badges={
        <>
          <Badge>ВМ</Badge>
          {/* Состояние питания домена снимается из virsh на hub'е. */}
          <PowerStateBadge state={vm.power_state} />
          <ReachSignal
            label="ping"
            reachable={vm.ping_reachable}
            latencyMs={vm.ping_latency_ms}
          />
          <ReachSignal label="ssh" reachable={vm.ssh_reachable} />
          {unreachable && (
            <Badge kind="danger" title="домен включён, но гость не отвечает ни по ping, ни по ssh">
              недоступна
            </Badge>
          )}
        </>
      }
      meta={
        <>
          <span className="mono">{vm.id}</span>
          <span>·</span>
          <span>
            № <b className="mono">{vm.number ?? "—"}</b>
          </span>
          <span>·</span>
          <span>
            хаб: <b>{hubLabel}</b>
          </span>
          <span>·</span>
          <span>
            dept: <b>{deptLabel}</b>
          </span>
        </>
      }
    >
      <VmReserveControl
        vm={vm}
        mock={mock}
        canManage={canManage}
        onLocal={onLocalUpdate}
        onChanged={onChanged}
      />
    </EntityHeader>
  );
}

/**
 * Бронь сервера: индикатор «Забронировано: <кто>, <note>» и кнопка
 * «Забронировать» / «Снять бронь». Под капотом busy-lease
 * (`POST/DELETE /servers/{id}/busy`). Снять чужую бронь может admin/dep_admin,
 * свою — оператор; backend перепроверит, клиентский гейт лишь прячет заведомо
 * лишнюю кнопку.
 */
function ServerHeaderPowerControls({
  server,
  onChanged,
}: {
  server: Server;
  onChanged?: () => void;
}) {
  const { persona } = usePersona();
  const toast = useToast();
  const { confirm } = useConfirm();
  const [pending, setPending] = useState<"toggle" | "reboot" | null>(null);

  const serverRole = persona.service_roles.server;
  const canPower =
    serverRole === "admin" ||
    serverRole === "operator" ||
    (isDepAdmin(persona) && persona.dept_id === server.department_id);
  const powerOnNow = server.power_state === "on";
  const toggleTitle = powerOnNow ? "Выключить сервер" : "Включить сервер";
  const denyReason = "Нет прав на управление питанием";

  async function handleTogglePower() {
    if (pending || !canPower) return;
    if (
      powerOnNow &&
      !(await confirm({
        title: "Выключить сервер",
        message: `Hard power off для ${server.hostname}? Соединения SSH и тесты будут оборваны.`,
        confirmLabel: "Выключить",
        danger: true,
      }))
    )
      return;
    setPending("toggle");
    try {
      const res = await (powerOnNow ? powerOff(server.id) : powerOn(server.id));
      toast.success(`${toggleTitle}: задача поставлена ${res.task_id}`);
      onChanged?.();
    } catch (e) {
      toast.error(apiErrMsg(e, `${toggleTitle} не отправлено`));
    } finally {
      setPending(null);
    }
  }

  async function handleReboot() {
    if (pending || !canPower) return;
    if (
      !(await confirm({
        title: "Перезагрузить сервер",
        message: `Перезагрузить ${server.hostname} через BMC power-cycle?`,
        confirmLabel: "Перезагрузить",
        danger: true,
      }))
    )
      return;
    setPending("reboot");
    try {
      const res = await powerReboot(server.id);
      toast.success(`Перезагрузка: задача поставлена ${res.task_id}`);
      onChanged?.();
    } catch (e) {
      toast.error(apiErrMsg(e, "Перезагрузка не отправлена"));
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="flex items-center gap-1">
      <Button
        type="button"
        size="sm"
        variant={powerOnNow ? "danger" : "primary"}
        className="w-8 px-0 flex items-center justify-center"
        onClick={handleTogglePower}
        disabled={!canPower || pending !== null}
        aria-label={toggleTitle}
        title={canPower ? toggleTitle : denyReason}
      >
        {powerOnNow ? (
          <PowerOff className="w-4 h-4" />
        ) : (
          <Power className="w-4 h-4" />
        )}
      </Button>
      <Button size="sm"
        type="button"
        className="w-8 px-0 flex items-center justify-center"
        onClick={handleReboot}
        disabled={!canPower || pending !== null}
        aria-label="Перезагрузить сервер"
        title={canPower ? "Перезагрузить сервер" : denyReason}
      >
        <RefreshCw className={`w-4 h-4 ${pending === "reboot" ? "animate-spin" : ""}`} />
      </Button>
    </div>
  );
}

function ServerReserveControl({
  server,
  onServerUpdated,
  onBusyChanged,
}: {
  server: Server;
  onServerUpdated: (next: Server) => void;
  onBusyChanged?: () => void;
}) {
  const { persona } = usePersona();
  const toast = useToast();
  const { prompt, confirm } = useConfirm();
  const [pending, setPending] = useState(false);

  const reserverLabel = useUserLabel(server.busy_user_id);

  const reserved = server.busy_state !== "free" || !!server.busy_note;

  const serverRole = persona.service_roles.server;
  const canOperate =
    isDepAdmin(persona) || serverRole === "admin" || serverRole === "operator";
  // Снятие брони показываем оператору+; фактическое право (своя/чужая бронь)
  // проверяет backend — release_busy на чужой лиз отбивает 403/409.
  const canRelease = canOperate;

  async function handleReserve() {
    if (pending || !canOperate) return;
    const { ok, reason } = await prompt({
      title: "Забронировать сервер",
      message: `Забронировать ${server.hostname}? Бронь блокирует деструктивные операции других пользователей до её снятия.`,
      reason: true,
      reasonLabel: "Примечание (зачем бронь)",
      reasonPlaceholder: "например, ручной debug-цикл",
      reasonRequired: true,
      confirmLabel: "Забронировать",
    });
    if (!ok) return;
    setPending(true);
    try {
      const next = await setBusy(server.id, { reason: reason.trim() });
      onServerUpdated(next);
      onBusyChanged?.();
      toast.success(`Сервер ${server.hostname} забронирован`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось забронировать"));
    } finally {
      setPending(false);
    }
  }

  async function handleRelease() {
    if (pending || !canRelease) return;
    if (
      !(await confirm({
        title: "Снять бронь",
        message: `Снять бронь с ${server.hostname}? Сервер освободится для других.`,
        confirmLabel: "Снять бронь",
      }))
    )
      return;
    setPending(true);
    try {
      const next = await clearBusy(server.id);
      onServerUpdated(next);
      onBusyChanged?.();
      toast.success(`Бронь с ${server.hostname} снята`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось снять бронь"));
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      {reserved ? (
        <>
          <Badge kind="warn" className="flex items-center gap-1">
            <Lock className="w-3.5 h-3.5" />
            Забронировано
            {server.busy_user_id && <>: {reserverLabel}</>}
          </Badge>
          {server.busy_note && (
            <span className="text-xs text-dim truncate max-w-[320px]">
              {server.busy_note}
            </span>
          )}
          {canRelease && (
            <Button size="sm"
              type="button"
              className="flex items-center gap-1"
              onClick={handleRelease}
              disabled={pending}
              title="Снять бронь"
            >
              <Unlock className="w-3.5 h-3.5" /> Снять бронь
            </Button>
          )}
        </>
      ) : (
        canOperate && (
          <Button variant="primary" size="sm"
            type="button"
            className="flex items-center gap-1"
            onClick={handleReserve}
            disabled={pending}
            title="Забронировать сервер"
          >
            <Lock className="w-3.5 h-3.5" /> Забронировать
          </Button>
        )
      )}
    </>
  );
}

/**
 * Бронь ВМ: индикатор «Забронировано» и кнопки брони. Оптимистично правит
 * локальную копию и дёргает `onChanged`, чтобы список хаба обновил индикатор.
 * Право на бронь приходит сверху одним флагом — у ВМ нет per-операционной
 * матрицы прав, как у сервера.
 */
function VmReserveControl({
  vm,
  mock,
  canManage,
  onLocal,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  canManage: boolean;
  onLocal: (next: Vm) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { prompt, confirm } = useConfirm();
  const [pending, setPending] = useState(false);
  // Пока busy_state непустой — ВМ занята lifecycle-локом (создание/удаление/…).
  // Бронь под тест — отдельно, через `status`.
  const busyLabel = vmBusyLabel(vm.busy_state);
  const reserved = vm.status !== "free" && vm.status !== "error";

  async function handleReserve() {
    if (pending || !canManage) return;
    const { ok, reason } = await prompt({
      title: "Забронировать ВМ",
      message: `Забронировать ${vm.name}? Бронь блокирует деструктивные операции других пользователей до её снятия.`,
      reason: true,
      reasonLabel: "Примечание (зачем бронь)",
      reasonPlaceholder: "например, ручной debug-цикл",
      reasonRequired: true,
      confirmLabel: "Забронировать",
    });
    if (!ok) return;
    setPending(true);
    try {
      const next = mock
        ? {
            ...vm,
            busy_state: null,
            busy_note: reason.trim(),
            status: reason.trim(),
          }
        : await reserveVm(vm.id, { reason: reason.trim() });
      onLocal(next as Vm);
      onChanged();
      toast.success(`ВМ ${vm.name} забронирована`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось забронировать"));
    } finally {
      setPending(false);
    }
  }

  async function handleRelease() {
    if (pending || !canManage) return;
    const ok = await confirm({
      title: "Снять бронь",
      message: `Снять бронь с ${vm.name}?`,
      confirmLabel: "Снять бронь",
    });
    if (!ok) return;
    setPending(true);
    try {
      const next = mock
        ? { ...vm, busy_state: null, busy_note: null, status: "free" }
        : await releaseVm(vm.id);
      onLocal(next as Vm);
      onChanged();
      toast.success(`Бронь с ${vm.name} снята`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось снять бронь"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mt-3 flex items-center gap-3 flex-wrap">
      {busyLabel ? (
        <Badge kind="warn" className="flex items-center gap-1">
          <RefreshCw className="w-3.5 h-3.5 animate-spin" /> {busyLabel}…
        </Badge>
      ) : reserved ? (
        <>
          <Badge kind="warn" className="flex items-center gap-1">
            <Lock className="w-3.5 h-3.5" /> Забронировано
          </Badge>
          {vm.busy_note && (
            <span className="text-xs text-dim truncate max-w-[320px]">
              {vm.busy_note}
            </span>
          )}
          {canManage && (
            <Button size="sm"
              type="button"
              className="flex items-center gap-1"
              onClick={handleRelease}
              disabled={pending}
              title="Снять бронь"
            >
              <Unlock className="w-3.5 h-3.5" /> Снять бронь
            </Button>
          )}
        </>
      ) : (
        canManage && (
          <Button variant="primary" size="sm"
            type="button"
            className="flex items-center gap-1"
            onClick={handleReserve}
            disabled={pending}
            title="Забронировать ВМ"
          >
            <Lock className="w-3.5 h-3.5" /> Забронировать
          </Button>
        )
      )}
    </div>
  );
}
