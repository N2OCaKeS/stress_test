/**
 * Hardware-вкладка карточки сервера.
 *
 * Read-only сводка по железу: CPU brand/model/cores/threads/freq, RAM total,
 * сеть (interface name), список дисков из `Server.storage`. Edit-кнопка
 * открывает inline-форму с PATCH `/servers/{id}` по hardware-полям
 * (без редактирования дисков — для них нужен отдельный CRUD-поток,
 * `ServerUpdateRequest.storage` снапшотит весь массив и легко стирает данные;
 * вынесено в TODO бэка).
 */
import { useState } from "react";
import type { ReactNode } from "react";
import {
  AlertCircle,
  Cpu,
  HardDrive,
  MemoryStick,
  Network,
  Pencil,
  RefreshCw,
  type LucideIcon,
} from "lucide-react";
import type { DiskResponse, Server, ServerUpdateRequest } from "@/api/server/types";
import { listVmDisks, type Vm, type VmDisk, type VmNic } from "@/api/server/vms";
import { updateServer } from "@/api/server/servers";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { usePersona } from "@/contexts/PersonaContext";
import { isDepAdmin } from "@/lib/rbac";
import { MOCK_VM_DISKS } from "@/mocks/vm";
import { FormRow, StatRow } from "@/pages/admin/services/_inline";
import type { EntityRef } from "./_entity";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";

interface Props {
  entity?: EntityRef;
  serverId: string;
  server?: Server;
  /** Поднимает свежий объект в ServerDetail, чтобы header и соседние
   * вкладки обновились после PATCH без перезагрузки страницы. */
  onServerUpdated?: (next: Server) => void;
}

/** Одна карточка-сводка (CPU / RAM / Сеть / Диск) в двухколоночной сетке. */
interface HwCard {
  icon: LucideIcon;
  title: string;
  rows: { k: string; v: ReactNode }[];
}

/** Таблица физических дисков во всю ширину — есть только у сервера. */
interface HwDiskTable {
  disks: DiskResponse[];
  footnote: ReactNode;
}

/** Детализация NIC ВМ во всю ширину — зеркало серверной таблицы интерфейсов. */
interface HwNicTable {
  nics: VmNic[];
}

/** Таблица дисков ВМ (read-only) — `GET /vms/{id}/disks`; управление на «Дисках». */
interface HwVmDiskTable {
  disks: VmDisk[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

/** Нормализованная модель «Железа», одинаковая для сервера и ВМ. */
interface HardwareModel {
  /** Кнопка правки сверху; у read-only сущностей (ВМ) — undefined. */
  edit?: { canEdit: boolean; onEdit: () => void };
  cards: HwCard[];
  diskTable?: HwDiskTable;
  nicTable?: HwNicTable;
  vmDiskTable?: HwVmDiskTable;
  footer?: ReactNode;
}

export function HardwareTab(props: Props) {
  // Карточка ВМ рендерится тем же файлом: у ВМ железо read-only, правка
  // ресурсов живёт во вкладке «Обзор», поэтому серверный edit-поток не нужен.
  if (props.entity?.kind === "vm") {
    return <VmHardwareTab vm={props.entity.vm} mock={props.entity.mock} />;
  }
  return <ServerHardwareTab {...props} />;
}

/**
 * Hardware-вкладка ВМ: те же карточки-сводки, что у сервера, плюс детализация
 * NIC из `vm.nics` и read-only таблица дисков (`GET /vms/{id}/disks`).
 * Управление дисками остаётся на вкладке «Диски», здесь только просмотр.
 */
function VmHardwareTab({ vm, mock }: { vm: Vm; mock: boolean }) {
  const disksQ = useQuery<VmDisk[]>(
    async () => {
      if (mock) return MOCK_VM_DISKS[vm.id] ?? [];
      const res = await listVmDisks(vm.id);
      return res.items;
    },
    [vm.id, mock],
    { keepPreviousDataOnError: true },
  );

  return (
    <HardwareView
      model={vmModel(vm, {
        disks: disksQ.data ?? [],
        loading: disksQ.loading,
        error: disksQ.error ? apiErrMsg(disksQ.error, "Диски не загрузились") : null,
        onRetry: () => disksQ.refetch(),
      })}
    />
  );
}

function ServerHardwareTab({ server, onServerUpdated }: Props) {
  const [editing, setEditing] = useState(false);

  const view = server;
  const { persona } = usePersona();

  if (!view) {
    return <div className="p-5 text-sm text-dim">Нет данных по серверу.</div>;
  }

  // PATCH hardware-полей = server:update; есть у dep_admin своего dept,
  // server.admin и server.operator. account_admin отрезан в Server.tsx.
  const canEdit =
    (isDepAdmin(persona) && persona.dept_id === view.department_id) ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";

  if (editing && canEdit) {
    return (
      <HardwareEditForm
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
    <HardwareView
      model={serverModel(view, canEdit, () => setEditing(true))}
    />
  );
}

const dim = <span className="text-dim">—</span>;
const mono = (v: ReactNode) => <span className="mono">{v}</span>;

/** Собирает модель «Железа» из серверного объекта. */
function serverModel(
  server: Server,
  canEdit: boolean,
  onEdit: () => void,
): HardwareModel {
  const ramGb =
    server.ram_total_mb != null
      ? (server.ram_total_mb / 1024).toFixed(server.ram_total_mb % 1024 === 0 ? 0 : 1)
      : null;

  return {
    edit: { canEdit, onEdit },
    cards: [
      {
        icon: Cpu,
        title: "CPU",
        rows: [
          { k: "brand", v: server.cpu_brand ?? dim },
          { k: "model", v: server.cpu_model ? mono(server.cpu_model) : dim },
          { k: "cores", v: server.cpu_cores != null ? mono(server.cpu_cores) : dim },
          { k: "threads", v: server.cpu_threads != null ? mono(server.cpu_threads) : dim },
          {
            k: "frequency",
            v:
              server.cpu_frequency_ghz != null
                ? mono(`${server.cpu_frequency_ghz} GHz`)
                : dim,
          },
        ],
      },
      {
        icon: MemoryStick,
        title: "RAM",
        rows: [
          { k: "total_mb", v: server.ram_total_mb != null ? mono(server.ram_total_mb) : dim },
          { k: "total_gb", v: ramGb != null ? mono(`${ramGb} GB`) : dim },
        ],
      },
      {
        icon: Network,
        title: "Сеть",
        rows: [
          {
            k: "interface_name",
            v: server.network_interface_name ? mono(server.network_interface_name) : dim,
          },
          {
            k: "interfaces",
            v:
              server.network_interfaces && server.network_interfaces.length > 0 ? (
                <span className="flex flex-wrap gap-1">
                  {server.network_interfaces.map((iface) => (
                    <Badge key={iface} className="mono">
                      {iface}
                    </Badge>
                  ))}
                </span>
              ) : (
                dim
              ),
          },
          { k: "ip_address", v: mono(server.ip_address) },
          { k: "mgmt_ip_address", v: server.mgmt_ip_address ? mono(server.mgmt_ip_address) : dim },
        ],
      },
      {
        icon: HardDrive,
        title: "Диск",
        rows: (() => {
          const sys = server.storage.find((d) => d.is_system) ?? null;
          return [
            { k: "system_gb", v: sys ? mono(`${sys.size_gb} GB`) : dim },
            {
              k: "used",
              v:
                sys && sys.used_gb != null
                  ? mono(
                      `${sys.used_gb} GB${sys.used_percent != null ? ` (${sys.used_percent}%)` : ""}`,
                    )
                  : dim,
            },
            { k: "model", v: sys?.model ? mono(sys.model) : dim },
          ];
        })(),
      },
    ],
    diskTable: {
      disks: server.storage,
      footnote: (
        <>
          Редактирование дисков идёт через инвентаризацию (`inventory_sync`) —
          вручную сюда писать не нужно.
        </>
      ),
    },
  };
}

/**
 * Собирает модель «Железа» из VM-объекта — виртуальные ресурсы домена
 * (vCPU / RAM / сеть / системный диск), детализацию NIC (`vm.nics`) и read-only
 * таблицу дисков ВМ. Правка ресурсов (vCPU/RAM) — во вкладке «Обзор», управление
 * дисками — во вкладке «Диски», поэтому здесь ни edit, ни серверного disk-CRUD
 * нет: те же карточки/таблицы, read-only.
 */
function vmModel(vm: Vm, vmDisks: HwVmDiskTable): HardwareModel {
  const ramGb = (vm.ram_mb / 1024).toFixed(vm.ram_mb % 1024 === 0 ? 0 : 1);
  const nics = vm.nics ?? [];
  return {
    cards: [
      {
        icon: Cpu,
        title: "CPU",
        rows: [{ k: "vCPU", v: mono(vm.cpu) }],
      },
      {
        icon: MemoryStick,
        title: "RAM",
        rows: [
          { k: "total_mb", v: mono(vm.ram_mb) },
          { k: "total_gb", v: mono(`${ramGb} GB`) },
        ],
      },
      {
        icon: Network,
        title: "Сеть",
        rows: [
          { k: "mode", v: mono(vm.network_mode) },
          { k: "ip_address", v: mono(vm.ip_address ?? "— (авто / NAT)") },
          {
            k: "interfaces",
            v:
              vm.network_interfaces && vm.network_interfaces.length > 0 ? (
                <span className="flex flex-wrap gap-1">
                  {vm.network_interfaces.map((iface) => (
                    <Badge key={iface} className="mono">
                      {iface}
                    </Badge>
                  ))}
                </span>
              ) : (
                dim
              ),
          },
        ],
      },
      {
        icon: HardDrive,
        title: "Диск",
        rows: [
          { k: "system_gb", v: mono(`${vm.disk_gb} GB`) },
          { k: "box", v: mono(vm.box) },
          { k: "os_version", v: vm.os_version ? mono(vm.os_version) : dim },
        ],
      },
    ],
    nicTable: { nics },
    vmDiskTable: vmDisks,
    footer: (
      <>
        Дополнительные диски создаются во вкладке «Диски». Ресурсы (vCPU/RAM)
        правятся во вкладке «Обзор».
      </>
    ),
  };
}

/**
 * Презентационная часть «Железа». Ничего не знает про сервер/ВМ — рисует
 * нормализованную модель: карточки-сводки в сетке, опциональную таблицу
 * физдисков и опциональную кнопку правки.
 */
function HardwareView({ model }: { model: HardwareModel }) {
  return (
    <div className="p-5 flex flex-col gap-4">
      {model.edit && (
        <div className="flex justify-end">
          {model.edit.canEdit && (
            <Button variant="ghost"
              className="flex items-center gap-1"
              onClick={model.edit.onEdit}
            >
              <Pencil className="w-4 h-4" /> Изменить
            </Button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {model.cards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.title} className="card">
              <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
                <Icon className="w-4 h-4 text-accent" /> {card.title}
              </h3>
              {card.rows.map((r) => (
                <StatRow key={r.k} k={r.k} v={r.v} />
              ))}
            </div>
          );
        })}
      </div>

      {model.nicTable && <NicTableCard table={model.nicTable} />}

      {model.diskTable && <DiskTableCard table={model.diskTable} />}

      {model.vmDiskTable && <VmDiskTableCard table={model.vmDiskTable} />}

      {model.footer && <div className="text-[11px] text-dim">{model.footer}</div>}
    </div>
  );
}

/** Детализация NIC ВМ — зеркало серверной таблицы интерфейсов, read-only. */
function NicTableCard({ table }: { table: HwNicTable }) {
  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <Network className="w-4 h-4 text-accent" /> Сетевые интерфейсы
        <span className="text-xs text-dim font-normal">({table.nics.length})</span>
      </h3>
      {table.nics.length === 0 ? (
        <div className="text-sm text-dim">Интерфейсов нет.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-dim text-xs border-b border-token">
                <th className="text-left py-2 pr-3">Устройство</th>
                <th className="text-left py-2 pr-3">Модель</th>
                <th className="text-left py-2 pr-3">Режим</th>
                <th className="text-left py-2 pr-3">Мост</th>
                <th className="text-left py-2 pr-3">MAC</th>
                <th className="text-left py-2 pr-3">IP</th>
              </tr>
            </thead>
            <tbody>
              {table.nics.map((n) => (
                <tr
                  key={n.name}
                  className="border-b border-dashed border-token last:border-b-0"
                >
                  <td className="py-1.5 pr-3 mono">{n.name}</td>
                  <td className="py-1.5 pr-3 mono">{n.model}</td>
                  <td className="py-1.5 pr-3 mono">{n.network_mode}</td>
                  <td className="py-1.5 pr-3 mono">
                    {n.bridge ?? <span className="text-dim">—</span>}
                  </td>
                  <td className="py-1.5 pr-3 mono">
                    {n.mac ?? <span className="text-dim">—</span>}
                  </td>
                  <td className="py-1.5 pr-3 mono">
                    {n.ip_address ?? <span className="text-dim">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Read-only таблица дисков ВМ (`GET /vms/{id}/disks`), стиль серверной storage. */
function VmDiskTableCard({ table }: { table: HwVmDiskTable }) {
  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <HardDrive className="w-4 h-4 text-accent" /> Диски
        <span className="text-xs text-dim font-normal">({table.disks.length})</span>
      </h3>
      {table.loading && table.disks.length === 0 ? (
        <div className="text-sm text-dim">Загрузка…</div>
      ) : table.error && table.disks.length === 0 ? (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{table.error}</div>
            <Button variant="ghost" className="mt-2 flex items-center gap-1" onClick={table.onRetry}>
              <RefreshCw className="w-3.5 h-3.5" /> Повторить
            </Button>
          </div>
        </div>
      ) : table.disks.length === 0 ? (
        <div className="text-sm text-dim">Дисков нет.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-dim text-xs border-b border-token">
                <th className="text-left py-2 pr-3">Имя</th>
                <th className="text-left py-2 pr-3">Размер, ГБ</th>
                <th className="text-left py-2 pr-3">Устройство</th>
                <th className="text-left py-2 pr-3">ФС</th>
                <th className="text-left py-2 pr-3">Монтирование</th>
                <th className="text-left py-2 pr-3">Состояние</th>
                <th className="text-left py-2 pr-3">Тип</th>
              </tr>
            </thead>
            <tbody>
              {table.disks.map((d) => (
                <tr
                  key={d.id}
                  className="border-b border-dashed border-token last:border-b-0"
                >
                  <td className="py-1.5 pr-3 mono">{d.name}</td>
                  <td className="py-1.5 pr-3 mono">{d.size_gb}</td>
                  <td className="py-1.5 pr-3 mono">
                    {d.target_dev ?? <span className="text-dim">—</span>}
                  </td>
                  <td className="py-1.5 pr-3 mono">
                    {d.fs ?? <span className="text-dim">—</span>}
                  </td>
                  <td className="py-1.5 pr-3 mono">
                    {d.mount ?? <span className="text-dim">—</span>}
                  </td>
                  <td className="py-1.5 pr-3 mono">{d.state}</td>
                  <td className="py-1.5 pr-3">
                    {d.is_system ? (
                      <Badge kind="ok">system</Badge>
                    ) : (
                      <span className="text-dim">data</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-3 text-[11px] text-dim">
        Read-only срез. Создание/увеличение/удаление дисков — во вкладке «Диски».
      </div>
    </div>
  );
}

/** Таблица физдисков сервера — часть общей презентации, но ВМ её не передаёт. */
function DiskTableCard({ table }: { table: HwDiskTable }) {
  return (
    <div className="card">
      <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
        <HardDrive className="w-4 h-4 text-accent" /> Диски
        <span className="text-xs text-dim font-normal">({table.disks.length})</span>
      </h3>
      {table.disks.length === 0 ? (
        <div className="text-sm text-dim">Дисков в инвентаре нет.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-dim text-xs border-b border-token">
                <th className="text-left py-2 pr-3">Слот</th>
                <th className="text-left py-2 pr-3">Размер, ГБ</th>
                <th className="text-left py-2 pr-3">Занято, ГБ</th>
                <th className="text-left py-2 pr-3">Занято, %</th>
                <th className="text-left py-2 pr-3">Модель</th>
                <th className="text-left py-2 pr-3">Тип</th>
                <th className="text-left py-2 pr-3">id</th>
              </tr>
            </thead>
            <tbody>
              {table.disks.map((d) => (
                <tr key={d.id} className="border-b border-dashed border-token last:border-b-0">
                  <td className="py-1.5 pr-3 mono">{d.slot}</td>
                  <td className="py-1.5 pr-3 mono">{d.size_gb}</td>
                  <td className="py-1.5 pr-3 mono">
                    {d.used_gb != null ? d.used_gb : <span className="text-dim">—</span>}
                  </td>
                  <td className="py-1.5 pr-3 mono">
                    {d.used_percent != null ? (
                      `${d.used_percent}%`
                    ) : (
                      <span className="text-dim">—</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3">
                    {d.model ?? <span className="text-dim">—</span>}
                  </td>
                  <td className="py-1.5 pr-3">
                    {d.is_system ? (
                      <Badge kind="ok">system</Badge>
                    ) : (
                      <span className="text-dim">data</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 mono text-dim">{d.id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-3 text-[11px] text-dim">{table.footnote}</div>
    </div>
  );
}

function HardwareEditForm({
  initial,
  onCancel,
  onSaved,
}: {
  initial: Server;
  onCancel: () => void;
  onSaved: (next: Server) => void;
}) {
  const [cpuBrand, setCpuBrand] = useState(initial.cpu_brand ?? "");
  const [cpuModel, setCpuModel] = useState(initial.cpu_model ?? "");
  const [cpuCores, setCpuCores] = useState(
    initial.cpu_cores != null ? String(initial.cpu_cores) : "",
  );
  const [cpuThreads, setCpuThreads] = useState(
    initial.cpu_threads != null ? String(initial.cpu_threads) : "",
  );
  const [cpuFreq, setCpuFreq] = useState(
    initial.cpu_frequency_ghz != null ? String(initial.cpu_frequency_ghz) : "",
  );
  const [ramMb, setRamMb] = useState(
    initial.ram_total_mb != null ? String(initial.ram_total_mb) : "",
  );
  const [iface, setIface] = useState(initial.network_interface_name ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const parseIntField = (
    raw: string,
    name: string,
  ): { ok: true; value: number | null } | { ok: false; error: string } => {
    const t = raw.trim();
    if (t === "") return { ok: true, value: null };
    const n = Number(t);
    if (!Number.isInteger(n) || n < 0) {
      return { ok: false, error: `${name} должен быть целым неотрицательным` };
    }
    return { ok: true, value: n };
  };

  const parseFloatField = (
    raw: string,
    name: string,
  ): { ok: true; value: number | null } | { ok: false; error: string } => {
    const t = raw.trim();
    if (t === "") return { ok: true, value: null };
    const n = Number(t);
    if (!Number.isFinite(n) || n < 0) {
      return { ok: false, error: `${name} должен быть числом ≥ 0` };
    }
    return { ok: true, value: n };
  };

  const submit = async () => {
    setErr(null);
    const cores = parseIntField(cpuCores, "cpu_cores");
    if (!cores.ok) {
      setErr(cores.error);
      return;
    }
    const threads = parseIntField(cpuThreads, "cpu_threads");
    if (!threads.ok) {
      setErr(threads.error);
      return;
    }
    const freq = parseFloatField(cpuFreq, "cpu_frequency_ghz");
    if (!freq.ok) {
      setErr(freq.error);
      return;
    }
    const ram = parseIntField(ramMb, "ram_total_mb");
    if (!ram.ok) {
      setErr(ram.error);
      return;
    }

    const body: ServerUpdateRequest = {
      cpu_brand: cpuBrand.trim() ? cpuBrand.trim() : null,
      cpu_model: cpuModel.trim() ? cpuModel.trim() : null,
      cpu_cores: cores.value,
      cpu_threads: threads.value,
      cpu_frequency_ghz: freq.value,
      ram_total_mb: ram.value,
      network_interface_name: iface.trim() ? iface.trim() : null,
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
          <Pencil className="w-4 h-4 text-accent" /> Железо ·{" "}
          <span className="mono">{initial.hostname}</span>
        </h3>
        {err && <div className="alert-danger mb-2">{err}</div>}
        <div className="flex flex-col gap-3">
          <FormRow label="cpu_brand">
            <input
              className="input"
              value={cpuBrand}
              onChange={(e) => setCpuBrand(e.target.value)}
            />
          </FormRow>
          <FormRow label="cpu_model">
            <input
              className="input mono"
              value={cpuModel}
              onChange={(e) => setCpuModel(e.target.value)}
            />
          </FormRow>
          <div className="grid grid-cols-3 gap-3">
            <FormRow label="cpu_cores">
              <input
                className="input mono"
                value={cpuCores}
                onChange={(e) => setCpuCores(e.target.value)}
                inputMode="numeric"
              />
            </FormRow>
            <FormRow label="cpu_threads">
              <input
                className="input mono"
                value={cpuThreads}
                onChange={(e) => setCpuThreads(e.target.value)}
                inputMode="numeric"
              />
            </FormRow>
            <FormRow label="freq, GHz">
              <input
                className="input mono"
                value={cpuFreq}
                onChange={(e) => setCpuFreq(e.target.value)}
                inputMode="decimal"
              />
            </FormRow>
          </div>
          <FormRow label="ram_total_mb">
            <input
              className="input mono"
              value={ramMb}
              onChange={(e) => setRamMb(e.target.value)}
              inputMode="numeric"
            />
          </FormRow>
          <FormRow label="network_interface_name">
            <input
              className="input mono"
              value={iface}
              onChange={(e) => setIface(e.target.value)}
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
          Диски не редактируются вручную — backend заменяет весь массив
          целиком, реальное обновление идёт через inventory worker.
        </div>
      </div>
    </div>
  );
}
