/**
 * Overview-вкладка карточки сервера и ВМ.
 *
 * По умолчанию (без `entity` или `entity.kind === "server"`) — read-only сводка
 * по `Server`: имя, департамент, статус/busy/power, сеть, OS-версия, ключевые
 * timestamps. Под edit-кнопкой — inline-форма с PATCH `/servers/{id}`
 * (display_name, description-эквивалент location, IP, ssh_port, os_version_id)
 * для account_admin'а и dep_admin'а своего dept'а. Источник данных — prop
 * `server: Server`; после PATCH поднимаем свежий объект через
 * `onServerUpdated(next)`, чтобы header и соседние вкладки обновились без
 * перезагрузки.
 *
 * При `entity.kind === "vm"` рендерится карточка «Параметры» ВМ с правкой
 * cpu/ram (`VmOverviewView`): право управления и mock-режим приходят из
 * `entity`, свежую копию ВМ поднимаем наверх через `onEntityUpdated`.
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

export function OverviewTab({
  server,
  onServerUpdated,
  entity,
  onEntityUpdated,
}: Props) {
  const [editing, setEditing] = useState(false);
  const view = server;
  const { persona } = usePersona();

  if (entity?.kind === "vm") {
    return <VmOverviewView entity={entity} onEntityUpdated={onEntityUpdated} />;
  }

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
    <OverviewView
      server={view}
      canEdit={canEdit}
      onEdit={() => setEditing(true)}
    />
  );
}

function OverviewView({
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

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-start gap-3 mb-3">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-base">Идентификация</h3>
            <div className="text-xs text-dim">
              Базовые поля карточки сервера.
            </div>
          </div>
          {canEdit && (
            <button
              className="btn btn-ghost flex items-center gap-1"
              onClick={onEdit}
            >
              <Pencil className="w-4 h-4" /> Изменить
            </button>
          )}
        </div>
        <StatRow k="display_name" v={<span className="mono">{name}</span>} />
        <StatRow k="hostname" v={<span className="mono">{server.hostname}</span>} />
        <StatRow k="id" v={<span className="mono">{server.id}</span>} />
        <StatRow k="department" v={deptLabel} />
        <StatRow
          k="serial_number"
          v={server.serial_number ?? <span className="text-dim">—</span>}
        />
        <StatRow
          k="asset_tag"
          v={server.asset_tag ?? <span className="text-dim">—</span>}
        />
        <StatRow
          k="location"
          v={server.location ?? <span className="text-dim">—</span>}
        />
      </div>

      <div className="card">
        <h3 className="font-semibold text-base mb-3">Состояние</h3>
        <StatRow k="status" v={server.status} />
        <StatRow
          k="ping"
          v={
            <ReachValue
              reachable={server.ping_reachable}
              latencyMs={server.ping_latency_ms}
              checkedAt={server.ping_checked_at}
            />
          }
        />
        <StatRow
          k="ssh"
          v={
            <ReachValue
              reachable={server.ssh_reachable}
              latencyMs={server.ssh_latency_ms}
              checkedAt={server.ssh_checked_at}
            />
          }
        />
        <StatRow
          k="ipmi"
          v={
            <IpmiPowerValue
              state={server.ipmi_power_state}
              checkedAt={server.ipmi_checked_at}
            />
          }
        />
        <StatRow k="power_state" v={server.power_state} />
        <StatRow k="busy_state" v={server.busy_state} />
        <StatRow
          k="busy_user_id"
          v={
            server.busy_user_id ? (
              <span title={server.busy_user_id}>{busyUserLabel}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="busy_since"
          v={
            server.busy_since ? (
              <span className="mono">{formatMsk(server.busy_since)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="busy_note"
          v={server.busy_note ?? <span className="text-dim">—</span>}
        />
        <StatRow
          k="is_managed"
          v={server.is_managed ? "да" : "нет"}
        />
        <StatRow
          k="management_user"
          v={
            server.management_user ? (
              <span className="mono">{server.management_user}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
      </div>

      <div className="card">
        <h3 className="font-semibold text-base mb-3">Сеть и OS</h3>
        <StatRow
          k="ip_address"
          v={<span className="mono">{server.ip_address}</span>}
        />
        <StatRow
          k="mgmt_ip_address"
          v={
            server.mgmt_ip_address ? (
              <span className="mono">{server.mgmt_ip_address}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow k="ssh_port" v={<span className="mono">{server.ssh_port}</span>} />
        <StatRow
          k="network_interface_name"
          v={
            server.network_interface_name ? (
              <span className="mono">{server.network_interface_name}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="os_version"
          v={
            server.os_version_id ? (
              <OsVersionName
                osVersionId={server.os_version_id}
                securityMode={server.os_security_mode}
              />
            ) : (
              <span className="text-dim">не задана</span>
            )
          }
        />
        <StatRow
          k="os_last_synced_at"
          v={
            server.os_last_synced_at ? (
              <span className="mono">{formatMsk(server.os_last_synced_at)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
      </div>

      <div className="card">
        <h3 className="font-semibold text-base mb-3">Метки времени</h3>
        <StatRow
          k="created_at"
          v={<span className="mono">{formatMsk(server.created_at)}</span>}
        />
        <StatRow
          k="updated_at"
          v={<span className="mono">{formatMsk(server.updated_at)}</span>}
        />
        <StatRow
          k="prepared_at"
          v={
            server.prepared_at ? (
              <span className="mono">{formatMsk(server.prepared_at)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="decommissioned_at"
          v={
            server.decommissioned_at ? (
              <span className="mono">{formatMsk(server.decommissioned_at)}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
        <StatRow
          k="created_by"
          v={
            server.created_by ? (
              <span title={server.created_by}>{createdByLabel}</span>
            ) : (
              <span className="text-dim">—</span>
            )
          }
        />
      </div>
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
 * Overview-вкладка карточки ВМ. Read-only карточка «Параметры» с ключевыми
 * полями домена и кнопкой «Изменить CPU/RAM» под правом управления. Правка
 * ресурсов — 202-задача (`PATCH /vms/{id}`), исход показываем баннером. В
 * mock-режиме cpu/ram применяем локально и поднимаем свежую копию наверх.
 */
function VmOverviewView({
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

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-base">Параметры</h3>
          {canManage && (
            <button
              className="btn btn-sm flex items-center gap-1"
              onClick={() => setResourceModal(true)}
              title="Изменить vCPU и RAM"
            >
              <Cpu className="w-3.5 h-3.5" /> Изменить CPU/RAM
            </button>
          )}
        </div>
        <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1.5 text-sm">
          <Field k="Хаб" v={view.hub_server_id} mono />
          <Field k="ОС" v={view.os_version ?? "—"} />
          <Field k="box" v={view.box} />
          <Field k="Сеть" v={view.network_mode} />
          <Field k="IP-адрес" v={view.ip_address ?? "— (авто)"} mono />
          <Field k="vCPU" v={String(view.cpu)} />
          <Field k="RAM" v={`${view.ram_mb} МБ`} />
          <Field k="Диск" v={`${view.disk_gb} ГБ`} />
          <Field k="autostart" v={view.autostart ? "да" : "нет"} />
          <Field k="Стратегия кред" v={view.cred_strategy} />
          <Field k="Питание" v={view.power_state} />
          <Field k="Занятость" v={view.busy_state} />
        </dl>
        {resourceOutcome.tracked && (
          <TaskOutcomeBanner
            outcome={resourceOutcome.tracked}
            className="mt-3"
            successText="Ресурсы применены."
            onCancelled={resourceOutcome.reset}
          />
        )}
      </div>

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

function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-dim text-xs">{k}</dt>
      <dd className={mono ? "mono" : undefined}>{v}</dd>
    </>
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
