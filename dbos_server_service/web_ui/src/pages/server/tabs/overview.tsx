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
import { useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Cpu, Pencil } from "lucide-react";
import type {
  OsVersion,
  PowerState,
  Server,
  ServerUpdateRequest,
  TaskDispatchResponse,
} from "@/api/server/types";
import { updateServer } from "@/api/server/servers";
import { listOsVersions } from "@/api/server/osVersions";
import { updateVm } from "@/api/server/vms";
import type { Vm, VmUpdateRequest } from "@/api/server/vms";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useDeptLabel, useUserLabel } from "@/lib/labels";
import { formatMsk } from "@/lib/datetime";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { isDepAdmin } from "@/lib/rbac";
import { formatLatencyMs } from "@/pages/server/_serverShared";
import { FormRow, StatRow } from "@/pages/admin/services/_inline";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import type { EntityRef } from "./_entity";

interface Props {
  serverId?: string;
  server?: Server;
  /** Поднимает свежий объект в ServerDetail, чтобы header и соседние
   * вкладки обновились после PATCH без перезагрузки страницы. */
  onServerUpdated?: (next: Server) => void;
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
  entity,
  onEntityUpdated,
}: Props) {
  if (entity?.kind === "vm") {
    return <VmOverview entity={entity} onEntityUpdated={onEntityUpdated} />;
  }
  return <ServerOverview server={server} onServerUpdated={onServerUpdated} />;
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
}: {
  server?: Server;
  onServerUpdated?: (next: Server) => void;
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
    />
  );
}

function ServerOverviewView({
  server,
  canEdit,
  onEdit,
}: {
  server: Server;
  canEdit: boolean;
  onEdit: () => void;
}) {
  const deptLabel = useDeptLabel(server.department_id);
  const createdByLabel = useUserLabel(server.created_by);
  const busyUserLabel = useUserLabel(server.busy_user_id);
  const name = server.display_name ?? server.hostname;
  const dash = <span className="text-dim">—</span>;

  const identity: ParamRow[] = [
    { label: "display_name", value: <span className="mono">{name}</span> },
    { label: "hostname", value: <span className="mono">{server.hostname}</span> },
    { label: "id", value: <span className="mono">{server.id}</span> },
    { label: "department", value: deptLabel },
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

  const editAction = canEdit ? (
    <button
      className="btn btn-ghost flex items-center gap-1"
      onClick={onEdit}
    >
      <Pencil className="w-4 h-4" /> Изменить
    </button>
  ) : undefined;

  return (
    <div className="p-5 flex flex-col gap-4">
      <ParamCard
        title="Идентификация"
        subtitle="Базовые поля карточки сервера."
        action={editAction}
        rows={identity}
      />
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
  const [displayName, setDisplayName] = useState(initial.display_name ?? "");
  const [location, setLocation] = useState(initial.location ?? "");
  const [ip, setIp] = useState(initial.ip_address);
  const [sshPort, setSshPort] = useState(String(initial.ssh_port));
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
    const body: ServerUpdateRequest = {
      display_name: displayName.trim() ? displayName.trim() : null,
      location: location.trim() ? location.trim() : null,
      ip_address: ip.trim(),
      ssh_port: portNum,
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
          <FormRow label="display_name" hint="Пусто = очистить поле">
            <input
              className="input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={initial.hostname}
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
          <FormRow label="ssh_port">
            <input
              className="input mono"
              value={sshPort}
              onChange={(e) => setSshPort(e.target.value)}
              inputMode="numeric"
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
            <select
              className="input"
              value={osVersionId}
              onChange={(e) => setOsVersionId(e.target.value)}
            >
              <option value="">— не задано —</option>
              {osList.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </FormRow>
        </div>
        <div className="mt-4 flex gap-2 justify-end">
          <button className="btn" onClick={onCancel} disabled={pending}>
            Отмена
          </button>
          <button
            className="btn btn-primary"
            disabled={pending}
            onClick={submit}
          >
            Сохранить
          </button>
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
 * Overview-вкладка карточки ВМ. Строит модель «Параметры» из `Vm` и рендерит
 * общий `ParamCard` — тот же, что и серверная ветка. Кнопка «Изменить CPU/RAM»
 * в шапке карточки открывает `ResourcesModal` (202-задача `PATCH /vms/{id}`),
 * исход показываем баннером под строками. В mock-режиме cpu/ram применяем
 * локально и поднимаем свежую копию наверх.
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

  // vm prop меняется при refetch — подхватываем свежую копию.
  const view = local.id === vm.id ? local : vm;

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

  const rows: ParamRow[] = [
    { label: "Хаб", value: view.hub_server_id, mono: true },
    { label: "ОС", value: view.os_version ?? "—" },
    { label: "box", value: view.box },
    { label: "Сеть", value: view.network_mode },
    { label: "IP-адрес", value: view.ip_address ?? "— (авто)", mono: true },
    { label: "vCPU", value: String(view.cpu) },
    { label: "RAM", value: `${view.ram_mb} МБ` },
    { label: "Диск", value: `${view.disk_gb} ГБ` },
    { label: "autostart", value: view.autostart ? "да" : "нет" },
    { label: "Стратегия кред", value: view.cred_strategy },
    { label: "Питание", value: view.power_state },
    { label: "Занятость", value: view.busy_state },
  ];

  const resourceAction = canManage ? (
    <button
      className="btn btn-sm flex items-center gap-1"
      onClick={() => setResourceModal(true)}
      title="Изменить vCPU и RAM"
    >
      <Cpu className="w-3.5 h-3.5" /> Изменить CPU/RAM
    </button>
  ) : undefined;

  return (
    <div className="p-5 flex flex-col gap-4">
      <ParamCard title="Параметры" action={resourceAction} rows={rows}>
        {resourceOutcome.tracked && (
          <TaskOutcomeBanner
            outcome={resourceOutcome.tracked}
            className="mt-3"
            successText="Ресурсы применены."
            onCancelled={resourceOutcome.reset}
          />
        )}
      </ParamCard>

      {resourceModal && (
        <ResourcesModal
          vm={view}
          onClose={() => setResourceModal(false)}
          onSubmit={handleUpdateResources}
        />
      )}
    </div>
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
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!valid || !changed || submitting}
          >
            {submitting ? "Применяем…" : "Применить"}
          </button>
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
    <Dialog.Root
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal-content" aria-describedby={undefined}>
          <div className="modal-header">
            <Dialog.Title className="text-base font-semibold">
              {title}
            </Dialog.Title>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
