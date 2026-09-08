/**
 * Overview-вкладка карточки сервера и ВМ.
 *
 * Обе ветки строят нормализованную view-model (набор карточек-сводок со
 * строками поле→значение) и рендерят один и тот же презентационный
 * под-компонент `ParamCard`. Отличается только источник данных и поля/действия,
 * у которых нет серверного аналога.
 *
 * Серверная ветка (`entity` нет или `entity.kind === "server"`) — read-only
 * сводка по `Server`: идентификация, состояние/busy/power, сеть, OS-версия,
 * timestamps. Под edit-кнопкой — inline-форма с PATCH `/servers/{id}`
 * (display_name, location, IP, ssh_port, os_version_id) для account_admin'а и
 * dep_admin'а своего dept'а. Источник данных — prop `server: Server`; после
 * PATCH поднимаем свежий объект через `onServerUpdated(next)`, чтобы header и
 * соседние вкладки обновились без перезагрузки.
 *
 * VM-ветка (`entity.kind === "vm"`) строит модель из `Vm` (Хаб/ОС/box/сеть/IP/
 * ресурсы/питание/занятость) и рендерит тот же `ParamCard`. Действие «Изменить
 * CPU/RAM» живёт кнопкой в шапке карточки и открывает `ResourcesModal` (202-
 * задача `PATCH /vms/{id}`). Право управления и mock-режим приходят из `entity`,
 * свежую копию ВМ поднимаем наверх через `onEntityUpdated`.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Cpu, Pencil, RefreshCw } from "lucide-react";
import type {
  OsVersion,
  PowerState,
  Server,
  ServerUpdateRequest,
  TaskDispatchResponse,
} from "@/api/server/types";
import { inventorySync, updateServer } from "@/api/server/servers";
import { listOsVersions } from "@/api/server/osVersions";
import { updateVm, updateVmIdentity } from "@/api/server/vms";
import type { Vm, VmIdentityUpdateRequest, VmUpdateRequest } from "@/api/server/vms";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useDeptLabel, useUserLabel } from "@/lib/labels";
import { formatMsk } from "@/lib/datetime";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { isDepAdmin } from "@/lib/rbac";
import { formatLatencyMs } from "@/pages/server/_serverShared";
import { FormRow, StatRow } from "@/pages/admin/services/_inline";
import { Dropdown } from "@/components/ui/Dropdown";
import { isTerminalTaskStatus, useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import type { EntityRef } from "./_entity";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal as UIModal } from "@/components/ui/Modal";

interface Props {
  serverId?: string;
  server?: Server;
  /** Поднимает свежий объект в ServerDetail, чтобы header и соседние
   * вкладки обновились после PATCH без перезагрузки страницы. */
  onServerUpdated?: (next: Server) => void;
  onChanged?: () => void;
  /** Дискриминатор сущности: сервер или ВМ. Без него вкладка работает в
   * прежнем серверном режиме по `server`. */
  entity?: EntityRef;
  /** Локальное обновление карточки после мутации (для ВМ — свежие cpu/ram). */
  onEntityUpdated?: (next: Server | Vm) => void;
}

/**
 * Диспетчер без собственных хуков: выбирает ветку по типу сущности и делегирует
 * её ServerOverview / VmOverview. Обе кормят один презентационный `ParamCard`.
 * Хуки живут внутри веток, поэтому ветвление не нарушает rules-of-hooks.
 */
export function OverviewTab({
  server,
  onServerUpdated,
  onChanged,
  entity,
  onEntityUpdated,
}: Props) {
  if (entity?.kind === "vm") {
    return <VmOverview entity={entity} onEntityUpdated={onEntityUpdated} />;
  }
  return (
    <ServerOverview
      server={server}
      onServerUpdated={onServerUpdated}
      onChanged={onChanged}
    />
  );
}

// ── Общий презентационный под-компонент ─────────────────────────────────────

/** Строка сводки: подпись слева, значение справа. `mono` — моноширинное значение. */
interface ParamRow {
  label: string;
  value: ReactNode;
  mono?: boolean;
}

/**
 * Карточка-сводка: заголовок (с опциональным подзаголовком и кнопкой-действием
 * справа) и набор строк поле→значение. Ничего не знает про сервер или ВМ — обе
 * ветки кормят её готовой view-model. Доп. контент под строками (баннер задачи
 * и т.п.) идёт через children.
 */
function ParamCard({
  title,
  subtitle,
  action,
  rows,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  rows: ParamRow[];
  children?: ReactNode;
}) {
  return (
    <div className="card">
      {subtitle || action ? (
        <div className="flex items-start gap-3 mb-3">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-base">{title}</h3>
            {subtitle && <div className="text-xs text-dim">{subtitle}</div>}
          </div>
          {action}
        </div>
      ) : (
        <h3 className="font-semibold text-base mb-3">{title}</h3>
      )}
      {rows.map((r) => (
        <StatRow
          key={r.label}
          k={r.label}
          v={r.mono ? <span className="mono">{r.value}</span> : r.value}
        />
      ))}
      {children}
    </div>
  );
}

// ── Серверная ветка ─────────────────────────────────────────────────────────

function ServerOverview({
  server,
  onServerUpdated,
  onChanged,
}: {
  server?: Server;
  onServerUpdated?: (next: Server) => void;
  onChanged?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const { persona } = usePersona();
  const view = server;

  if (!view) {
    return <div className="p-5 text-sm text-dim">Нет данных по серверу.</div>;
  }

  // server:update — есть у dep_admin (своего dept), server.admin и server.operator
  // (по дефолтной матрице прав server_service). account_admin/logging_admin сюда
  // не доходят — Server.tsx отрезает их раньше.
  const canEdit =
    (isDepAdmin(persona) && persona.dept_id === view.department_id) ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";

  if (editing && canEdit) {
    return (
      <OverviewEditForm
        initial={view}
        onCancel={() => setEditing(false)}
        onSaved={(next) => {
          onServerUpdated?.(next);
          setEditing(false);
        }}
      />
    );
  }

  return (
    <ServerOverviewView
      server={view}
      canEdit={canEdit}
      onEdit={() => setEditing(true)}
      onChanged={onChanged}
    />
  );
}

function ServerOverviewView({
  server,
  canEdit,
  onEdit,
  onChanged,
}: {
  server: Server;
  canEdit: boolean;
  onEdit: () => void;
  onChanged?: () => void;
}) {
  const deptLabel = useDeptLabel(server.department_id);
  const createdByLabel = useUserLabel(server.created_by);
  const busyUserLabel = useUserLabel(server.busy_user_id);
  const name = server.display_name ?? server.hostname;
  const dash = <span className="text-dim">—</span>;
  const toast = useToast();
  const [syncing, setSyncing] = useState(false);
  const syncOutcome = useTaskOutcome();
  const autoSyncedRef = useRef<string | null>(null);

  useEffect(() => {
    if (syncOutcome.tracked?.status === "succeeded") onChanged?.();
  }, [syncOutcome.tracked?.status, onChanged]);

  async function handleInventorySync(silent = false) {
    if (syncing) return;
    setSyncing(true);
    try {
      const res = await inventorySync(server.id);
      if (!silent) toast.success(`inventory_sync поставлен в очередь: ${res.task_id}`);
      syncOutcome.track("inventory_sync", res.task_id, res.status);
      onChanged?.();
    } catch (e) {
      toast.error(apiErrMsg(e, "inventory_sync не запущен"));
    } finally {
      setSyncing(false);
    }
  }

  // Автозапуск inventory_sync при открытии вкладки — раз на сервер, без тоста.
  useEffect(() => {
    if (autoSyncedRef.current === server.id) return;
    autoSyncedRef.current = server.id;
    handleInventorySync(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server.id]);

  const identity: ParamRow[] = [
    { label: "display_name", value: <span className="mono">{name}</span> },
    { label: "hostname", value: <span className="mono">{server.hostname}</span> },
    { label: "id", value: <span className="mono">{server.id}</span> },
    { label: "department", value: deptLabel },
    {
      label: "number",
      value: server.number != null ? String(server.number) : dash,
    },
    { label: "mgmt_ip_address", value: server.mgmt_ip_address ?? dash },
    { label: "serial_number", value: server.serial_number ?? dash },
    { label: "asset_tag", value: server.asset_tag ?? dash },
    { label: "location", value: server.location ?? dash },
  ];

  const state: ParamRow[] = [
    { label: "status", value: server.status },
    {
      label: "ping",
      value: (
        <ReachValue
          reachable={server.ping_reachable}
          latencyMs={server.ping_latency_ms}
          checkedAt={server.ping_checked_at}
        />
      ),
    },
    {
      label: "ssh",
      value: (
        <ReachValue
          reachable={server.ssh_reachable}
          latencyMs={server.ssh_latency_ms}
          checkedAt={server.ssh_checked_at}
        />
      ),
    },
    {
      label: "ipmi",
      value: (
        <IpmiPowerValue
          state={server.ipmi_power_state}
          checkedAt={server.ipmi_checked_at}
        />
      ),
    },
    { label: "power_state", value: server.power_state },
    { label: "busy_state", value: server.busy_state },
    {
      label: "busy_user_id",
      value: server.busy_user_id ? (
        <span title={server.busy_user_id}>{busyUserLabel}</span>
      ) : (
        dash
      ),
    },
    {
      label: "busy_service_name",
      value: server.busy_actor_type === "service" && server.busy_service_name ? (
        <span className="mono">{server.busy_service_name}</span>
      ) : (
        dash
      ),
    },
    {
      label: "busy_since",
      value: server.busy_since ? (
        <span className="mono">{formatMsk(server.busy_since)}</span>
      ) : (
        dash
      ),
    },
    { label: "busy_note", value: server.busy_note ?? dash },
    { label: "is_managed", value: server.is_managed ? "да" : "нет" },
    {
      label: "management_user",
      value: server.management_user ? (
        <span className="mono">{server.management_user}</span>
      ) : (
        dash
      ),
    },
  ];

  const network: ParamRow[] = [
    { label: "ip_address", value: <span className="mono">{server.ip_address}</span> },
    {
      label: "mgmt_ip_address",
      value: server.mgmt_ip_address ? (
        <span className="mono">{server.mgmt_ip_address}</span>
      ) : (
        dash
      ),
    },
    { label: "ssh_port", value: <span className="mono">{server.ssh_port}</span> },
    {
      label: "network_interface_name",
      value: server.network_interface_name ? (
        <span className="mono">{server.network_interface_name}</span>
      ) : (
        dash
      ),
    },
    {
      label: "os_version",
      value: server.os_version_id ? (
        <OsVersionName
          osVersionId={server.os_version_id}
          securityMode={server.os_security_mode}
        />
      ) : (
        <span className="text-dim">не задана</span>
      ),
    },
    {
      label: "os_last_synced_at",
      value: server.os_last_synced_at ? (
        <span className="mono">{formatMsk(server.os_last_synced_at)}</span>
      ) : (
        dash
      ),
    },
  ];

  const timestamps: ParamRow[] = [
    {
      label: "created_at",
      value: <span className="mono">{formatMsk(server.created_at)}</span>,
    },
    {
      label: "updated_at",
      value: <span className="mono">{formatMsk(server.updated_at)}</span>,
    },
    {
      label: "prepared_at",
      value: server.prepared_at ? (
        <span className="mono">{formatMsk(server.prepared_at)}</span>
      ) : (
        dash
      ),
    },
    {
      label: "decommissioned_at",
      value: server.decommissioned_at ? (
        <span className="mono">{formatMsk(server.decommissioned_at)}</span>
      ) : (
        dash
      ),
    },
    {
      label: "created_by",
      value: server.created_by ? (
        <span title={server.created_by}>{createdByLabel}</span>
      ) : (
        dash
      ),
    },
  ];

  const editAction = (
    <div className="flex items-center gap-2 flex-wrap justify-end">
      <Button
        variant="ghost"
        className="flex items-center gap-1"
        onClick={() => handleInventorySync()}
        disabled={syncing}
      >
        <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
        Inventory sync
      </Button>
      {canEdit && (
        <Button variant="ghost" className="flex items-center gap-1" onClick={onEdit}>
          <Pencil className="w-4 h-4" /> Изменить
        </Button>
      )}
    </div>
  );

  return (
    <div className="p-5 flex flex-col gap-4">
      <ParamCard
        title="Идентификация"
        subtitle="Базовые поля карточки сервера."
        action={editAction}
        rows={identity}
      >
        {syncOutcome.tracked && (
          <div className="mt-3 text-xs text-dim">
            inventory_sync: <span className="mono">{syncOutcome.tracked.status}</span>
            {isTerminalTaskStatus(syncOutcome.tracked.status) && syncOutcome.tracked.error && (
              <span className="text-danger"> · {syncOutcome.tracked.error}</span>
            )}
          </div>
        )}
      </ParamCard>
      <ParamCard title="Состояние" rows={state} />
      <ParamCard title="Сеть и OS" rows={network} />
      <ParamCard title="Метки времени" rows={timestamps} />
    </div>
  );
}

/**
 * Значение строки доступности (ping / ssh): «доступен» + latency либо
 * «недоступен», плюс время последней пробы. reachable == null (пробы не было)
 * рисуем нейтральным «не проверялось».
 */
function ReachValue({
  reachable,
  latencyMs,
  checkedAt,
}: {
  reachable?: boolean | null;
  latencyMs?: number | null;
  checkedAt?: string | null;
}) {
  if (reachable == null) {
    return <span className="text-dim">не проверялось</span>;
  }
  const lat = formatLatencyMs(latencyMs);
  return (
    <span className="flex items-center gap-2 flex-wrap">
      <span className={reachable ? "text-ok" : "text-danger"}>
        {reachable ? "доступен" : "недоступен"}
      </span>
      {reachable && lat && <span className="mono text-dim">{lat}</span>}
      {checkedAt && (
        <span className="mono text-dim text-xs">{formatMsk(checkedAt)}</span>
      )}
    </span>
  );
}

/**
 * Значение строки питания по IPMI/BMC: on/off/unknown (латиницей, как enum) +
 * время последней пробы. null — пробы BMC ещё не было.
 */
function IpmiPowerValue({
  state,
  checkedAt,
}: {
  state?: PowerState | null;
  checkedAt?: string | null;
}) {
  if (state == null) {
    return <span className="text-dim">не проверялось</span>;
  }
  const kind =
    state === "on" ? "text-ok" : state === "off" ? "text-danger" : "text-dim";
  return (
    <span className="flex items-center gap-2 flex-wrap">
      <span className={kind}>
        питание: <span className="mono">{state}</span>
      </span>
      {checkedAt && (
        <span className="mono text-dim text-xs">{formatMsk(checkedAt)}</span>
      )}
    </span>
  );
}

/**
 * Резолвит `osv_*` в имя (версию) из каталога OS-версий; raw id — в title.
 * Если у сервера определён режим защищённости Astra (`securityMode`), дописывает
 * его после версии: «1.8.1.6 Smolensk». Пустой режим — показываем только версию.
 */
function OsVersionName({
  osVersionId,
  securityMode,
}: {
  osVersionId: string;
  securityMode?: string | null;
}) {
  const q = useQuery(() => listOsVersions({ limit: 200 }), []);
  const name = useMemo(
    () => q.data?.items.find((v) => v.id === osVersionId)?.name,
    [q.data, osVersionId],
  );
  const version = name ?? osVersionId;
  const label = securityMode ? `${version} ${securityMode}` : version;
  return (
    <span className="mono" title={osVersionId}>
      {label}
    </span>
  );
}

function OverviewEditForm({
  initial,
  onCancel,
  onSaved,
}: {
  initial: Server;
  onCancel: () => void;
  onSaved: (next: Server) => void;
}) {
  const [hostname, setHostname] = useState(initial.hostname);
  const [displayName, setDisplayName] = useState(initial.display_name ?? "");
  const [number, setNumber] = useState(
    initial.number != null ? String(initial.number) : "",
  );
  const [location, setLocation] = useState(initial.location ?? "");
  const [ip, setIp] = useState(initial.ip_address);
  const [mgmtIp, setMgmtIp] = useState(initial.mgmt_ip_address ?? "");
  const [sshPort, setSshPort] = useState(String(initial.ssh_port));
  const [serialNumber, setSerialNumber] = useState(initial.serial_number ?? "");
  const [assetTag, setAssetTag] = useState(initial.asset_tag ?? "");
  const [osVersionId, setOsVersionId] = useState(initial.os_version_id ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const osVersions = useQuery(() => listOsVersions({ limit: 200 }), []);
  const osList: OsVersion[] = useMemo(
    () => osVersions.data?.items ?? [],
    [osVersions.data],
  );

  const submit = async () => {
    setErr(null);
    const portNum = Number(sshPort);
    if (!Number.isInteger(portNum) || portNum <= 0 || portNum > 65535) {
      setErr("ssh_port должен быть целым числом 1..65535");
      return;
    }
    let numberVal: number | null = null;
    if (number.trim()) {
      numberVal = Number(number);
      if (!Number.isInteger(numberVal) || numberVal < 0) {
        setErr("number должен быть целым числом ≥ 0");
        return;
      }
    }
    if (!hostname.trim()) {
      setErr("hostname не может быть пустым");
      return;
    }
    const body: ServerUpdateRequest = {
      hostname: hostname.trim(),
      display_name: displayName.trim() ? displayName.trim() : null,
      number: numberVal,
      location: location.trim() ? location.trim() : null,
      ip_address: ip.trim(),
      mgmt_ip_address: mgmtIp.trim() ? mgmtIp.trim() : null,
      ssh_port: portNum,
      serial_number: serialNumber.trim() ? serialNumber.trim() : null,
      asset_tag: assetTag.trim() ? assetTag.trim() : null,
      os_version_id: osVersionId ? osVersionId : null,
    };
    setPending(true);
    try {
      const next = await updateServer(initial.id, body);
      onSaved(next);
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="p-5">
      <div className="card w-full">
        <h3 className="font-semibold mb-3 flex items-center gap-2">
          <Pencil className="w-4 h-4 text-accent" /> Изменить ·{" "}
          <span className="mono">{initial.hostname}</span>
        </h3>
        {err && <div className="alert-danger mb-2">{err}</div>}
        <div className="flex flex-col gap-3">
          <FormRow label="hostname" hint="UNIQUE. Конфликт с другим сервером → ошибка при сохранении">
            <input
              className="input mono"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
            />
          </FormRow>
          <FormRow label="display_name" hint="Пусто = очистить поле">
            <input
              className="input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={initial.hostname}
            />
          </FormRow>
          <FormRow label="number" hint="Номер стенда, UNIQUE в паре servers+vm. Пусто = снять номер">
            <input
              className="input mono"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
              inputMode="numeric"
            />
          </FormRow>
          <FormRow label="location" hint="Стойка / DC / комната">
            <input
              className="input"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
            />
          </FormRow>
          <FormRow label="ip_address">
            <input
              className="input mono"
              value={ip}
              onChange={(e) => setIp(e.target.value)}
            />
          </FormRow>
          <FormRow label="mgmt_ip_address" hint="BMC/iDRAC-адрес, если отделён от основного. Пусто = очистить">
            <input
              className="input mono"
              value={mgmtIp}
              onChange={(e) => setMgmtIp(e.target.value)}
            />
          </FormRow>
          <FormRow label="ssh_port">
            <input
              className="input mono"
              value={sshPort}
              onChange={(e) => setSshPort(e.target.value)}
              inputMode="numeric"
            />
          </FormRow>
          <FormRow label="serial_number" hint="UNIQUE, если задан. Пусто = очистить">
            <input
              className="input mono"
              value={serialNumber}
              onChange={(e) => setSerialNumber(e.target.value)}
            />
          </FormRow>
          <FormRow label="asset_tag" hint="Инвентарный номер. Пусто = очистить">
            <input
              className="input mono"
              value={assetTag}
              onChange={(e) => setAssetTag(e.target.value)}
            />
          </FormRow>
          <FormRow
            label="os_version_id"
            hint={
              osVersions.loading
                ? "Загружаем каталог…"
                : "Пусто = сбросить версию"
            }
          >
            <Dropdown
              mode="single"
              searchable
              placeholder="— не задано —"
              options={[{ value: "", label: "— не задано —" }, ...osList.map((v) => ({ value: v.id, label: v.name }))]}
              value={osVersionId}
              onChange={setOsVersionId}
            />
          </FormRow>
        </div>
        <div className="mt-4 flex gap-2 justify-end">
          <Button onClick={onCancel} disabled={pending}>
            Отмена
          </Button>
          <Button variant="primary"
            disabled={pending}
            onClick={submit}
          >
            Сохранить
          </Button>
        </div>
        <div className="mt-3 text-[11px] text-dim">
          Смена департамента и hardware-поля редактируются отдельно (вкладка
          «Железо» и admin-flow для dept).
        </div>
      </div>
    </div>
  );
}

// ── VM-ветка ────────────────────────────────────────────────────────────────

/** Сущность-ВМ, суженная из общего `EntityRef`. */
type VmEntity = Extract<EntityRef, { kind: "vm" }>;

/** Заглушка диспатча для mock-режима: в backend не ходим. */
function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
}

/**
 * Overview-вкладка карточки ВМ. Строит те же четыре секции-сводки, что и
 * серверная ветка (`Идентификация` / `Состояние` / `Сеть и OS` / `Метки
 * времени`), и рендерит общий `ParamCard`. Поля, которых у ВМ нет, рисуем «—»
 * — секции не выкидываем ради визуального паритета с сервером. Кнопка «Изменить
 * CPU/RAM» живёт в шапке `Идентификации` и открывает `ResourcesModal`
 * (202-задача `PATCH /vms/{id}`); исход показываем баннером под строками. В
 * mock-режиме cpu/ram применяем локально и поднимаем свежую копию наверх.
 */
function VmOverview({
  entity,
  onEntityUpdated,
}: {
  entity: VmEntity;
  onEntityUpdated?: (next: Server | Vm) => void;
}) {
  const { vm, mock, canManage, onChanged } = entity;
  const toast = useToast();
  const resourceOutcome = useTaskOutcome();
  const [local, setLocal] = useState<Vm>(vm);
  const [resourceModal, setResourceModal] = useState(false);
  const [identityModal, setIdentityModal] = useState(false);

  // vm prop меняется при refetch — подхватываем свежую копию.
  const view = local.id === vm.id ? local : vm;

  const deptLabel = useDeptLabel(view.department_id);
  const createdByLabel = useUserLabel(view.created_by ?? null);
  const busyUserLabel = useUserLabel(view.busy_user_id ?? null);
  const dash = <span className="text-dim">—</span>;

  async function handleUpdateResources(body: VmUpdateRequest) {
    resourceOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await updateVm(view.id, body);
      resourceOutcome.track(
        `update cpu/ram · ${view.name}`,
        res.task_id,
        res.status,
      );
      toast.success(`Изменение ресурсов ${view.name} — задача поставлена`);
      setResourceModal(false);
      if (mock) {
        const next: Vm = {
          ...view,
          cpu: body.cpu ?? view.cpu,
          ram_mb: body.ram_mb ?? view.ram_mb,
        };
        setLocal(next);
        onEntityUpdated?.(next);
      }
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось изменить ресурсы"));
    }
  }

  async function handleUpdateIdentity(body: VmIdentityUpdateRequest) {
    try {
      const next = mock ? { ...view, ...body } : await updateVmIdentity(view.id, body);
      toast.success(`Карточка ${next.name} обновлена`);
      setIdentityModal(false);
      setLocal(next);
      onEntityUpdated?.(next);
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось изменить карточку ВМ"));
    }
  }

  const identity: ParamRow[] = [
    { label: "display_name", value: <span className="mono">{view.name}</span> },
    {
      label: "hostname",
      value: view.hostname ? <span className="mono">{view.hostname}</span> : dash,
    },
    { label: "id", value: <span className="mono">{view.id}</span> },
    { label: "department", value: deptLabel },
    { label: "number", value: view.number != null ? String(view.number) : dash },
  ];

  const state: ParamRow[] = [
    { label: "status", value: view.status },
    { label: "power_state", value: view.power_state },
    {
      label: "ping",
      value: (
        <ReachValue
          reachable={view.ping_reachable}
          latencyMs={view.ping_latency_ms}
          checkedAt={view.ping_checked_at}
        />
      ),
    },
    {
      label: "ssh",
      value: (
        <ReachValue reachable={view.ssh_reachable} checkedAt={view.ssh_checked_at} />
      ),
    },
    { label: "last_error", value: view.last_error ? view.last_error : dash },
    { label: "is_managed", value: view.is_managed ? "да" : "нет" },
    {
      label: "mgmt_user",
      value: view.mgmt_user ? <span className="mono">{view.mgmt_user}</span> : dash,
    },
    { label: "busy_state", value: view.busy_state },
    {
      label: "busy_user_id",
      value: view.busy_user_id ? (
        <span title={view.busy_user_id}>{busyUserLabel}</span>
      ) : (
        dash
      ),
    },
    {
      label: "busy_since",
      value: view.busy_since ? (
        <span className="mono">{formatMsk(view.busy_since)}</span>
      ) : (
        dash
      ),
    },
    { label: "busy_note", value: view.busy_note ? view.busy_note : dash },
  ];

  const network: ParamRow[] = [
    {
      label: "ip_address",
      value: view.ip_address ? (
        <span className="mono">{view.ip_address}</span>
      ) : (
        <span className="text-dim">— (авто)</span>
      ),
    },
    { label: "network_mode", value: <span className="mono">{view.network_mode}</span> },
    {
      label: "os_version",
      value: view.os_version ? (
        <span className="mono">{view.os_version}</span>
      ) : (
        <span className="text-dim">не задана</span>
      ),
    },
    { label: "box", value: <span className="mono">{view.box}</span> },
    {
      label: "nics",
      value:
        view.nics && view.nics.length > 0 ? (
          <span className="flex flex-wrap gap-1">
            {view.nics.map((nic) => (
              <Badge key={nic.name} className="mono">
                {nic.name}/{nic.model}
                {nic.bridge ? `@${nic.bridge}` : ""}
              </Badge>
            ))}
          </span>
        ) : (
          dash
        ),
    },
  ];

  const timestamps: ParamRow[] = [
    {
      label: "created_at",
      value: <span className="mono">{formatMsk(view.created_at)}</span>,
    },
    {
      label: "updated_at",
      value: <span className="mono">{formatMsk(view.updated_at)}</span>,
    },
    {
      label: "created_by",
      value: view.created_by ? (
        <span title={view.created_by}>{createdByLabel}</span>
      ) : (
        dash
      ),
    },
  ];

  const identityActions = canManage ? (
    <div className="flex items-center gap-2">
      <Button size="sm"
        className="flex items-center gap-1"
        onClick={() => setIdentityModal(true)}
        title="Изменить name/number"
      >
        <Pencil className="w-3.5 h-3.5" /> Изменить
      </Button>
      <Button size="sm"
        className="flex items-center gap-1"
        onClick={() => setResourceModal(true)}
        title="Изменить vCPU и RAM"
      >
        <Cpu className="w-3.5 h-3.5" /> Изменить CPU/RAM
      </Button>
    </div>
  ) : undefined;

  return (
    <div className="p-5 flex flex-col gap-4">
      <ParamCard
        title="Идентификация"
        subtitle="Базовые поля карточки ВМ. Ресурсы (vCPU/RAM) — во вкладке «Железо»."
        action={identityActions}
        rows={identity}
      >
        {resourceOutcome.tracked && (
          <TaskOutcomeBanner
            outcome={resourceOutcome.tracked}
            className="mt-3"
            successText="Ресурсы применены."
            onCancelled={resourceOutcome.reset}
          />
        )}
      </ParamCard>
      <ParamCard title="Состояние" rows={state} />
      <ParamCard title="Сеть и OS" rows={network} />
      <ParamCard title="Метки времени" rows={timestamps} />

      {resourceModal && (
        <ResourcesModal
          vm={view}
          onClose={() => setResourceModal(false)}
          onSubmit={handleUpdateResources}
        />
      )}
      {identityModal && (
        <IdentityModal
          vm={view}
          onClose={() => setIdentityModal(false)}
          onSubmit={handleUpdateIdentity}
        />
      )}
    </div>
  );
}

function IdentityModal({
  vm,
  onClose,
  onSubmit,
}: {
  vm: Vm;
  onClose: () => void;
  onSubmit: (body: VmIdentityUpdateRequest) => void | Promise<void>;
}) {
  const [name, setName] = useState(vm.name);
  const [number, setNumber] = useState(vm.number != null ? String(vm.number) : "");
  const [err, setErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr("name не может быть пустым");
      return;
    }
    let numberVal: number | null = null;
    if (number.trim()) {
      numberVal = Number(number);
      if (!Number.isInteger(numberVal) || numberVal < 0) {
        setErr("number должен быть целым числом ≥ 0");
        return;
      }
    }
    setSubmitting(true);
    try {
      await Promise.resolve(
        onSubmit({ name: name.trim(), number: numberVal }),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Изменить · ${vm.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          {err && <div className="alert-danger text-xs">{err}</div>}
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">name * · UNIQUE в пределах hub'а</span>
            <input
              className="input mono"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-dim text-xs">
              number · UNIQUE в паре servers+vm, пусто = снять номер
            </span>
            <input
              className="input mono"
              inputMode="numeric"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
            />
          </label>
          <div className="text-[11px] text-dim">
            hostname гостя (`hostnamectl`) здесь не меняется — это отдельная
            операция внутри гостя, не карточечное поле.
          </div>
        </div>
        <div className="modal-footer">
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {submitting ? "Сохраняем…" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ResourcesModal({
  vm,
  onClose,
  onSubmit,
}: {
  vm: Vm;
  onClose: () => void;
  onSubmit: (body: VmUpdateRequest) => void | Promise<void>;
}) {
  const [cpu, setCpu] = useState(String(vm.cpu));
  const [ramMb, setRamMb] = useState(String(vm.ram_mb));
  const [submitting, setSubmitting] = useState(false);

  const cpuN = Number.parseInt(cpu, 10);
  const ramN = Number.parseInt(ramMb, 10);
  const valid =
    Number.isFinite(cpuN) && cpuN > 0 && Number.isFinite(ramN) && ramN >= 256;
  const changed = cpuN !== vm.cpu || ramN !== vm.ram_mb;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid || !changed || submitting) return;
    const body: VmUpdateRequest = {};
    if (cpuN !== vm.cpu) body.cpu = cpuN;
    if (ramN !== vm.ram_mb) body.ram_mb = ramN;
    setSubmitting(true);
    try {
      await Promise.resolve(onSubmit(body));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Ресурсы ВМ ${vm.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body flex flex-col gap-3">
          <div className="text-xs text-dim">
            Изменение остановит ВМ, применит новые значения и запустит её заново.
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">vCPU *</span>
              <input
                className="input"
                type="number"
                min={1}
                value={cpu}
                onChange={(e) => setCpu(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">RAM, МБ *</span>
              <input
                className="input"
                type="number"
                min={256}
                step={256}
                value={ramMb}
                onChange={(e) => setRamMb(e.target.value)}
              />
            </label>
          </div>
        </div>
        <div className="modal-footer">
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary"
            type="submit"
            disabled={!valid || !changed || submitting}
          >
            {submitting ? "Применяем…" : "Применить"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <UIModal open onOpenChange={(next) => !next && onClose()} title={title}>
      {children}
    </UIModal>
  );
}
