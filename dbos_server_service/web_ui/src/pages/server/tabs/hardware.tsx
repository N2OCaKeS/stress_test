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
  Cpu,
  HardDrive,
  MemoryStick,
  Network,
  Pencil,
  type LucideIcon,
} from "lucide-react";
import type { DiskResponse, Server, ServerUpdateRequest } from "@/api/server/types";
import type { Vm } from "@/api/server/vms";
import { updateServer } from "@/api/server/servers";
import { apiErrMsg } from "@/api/client";
import { usePersona } from "@/contexts/PersonaContext";
import { isDepAdmin } from "@/lib/rbac";
import { FormRow, StatRow } from "@/pages/admin/services/_inline";
import type { EntityRef } from "./_entity";

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

/** Нормализованная модель «Железа», одинаковая для сервера и ВМ. */
interface HardwareModel {
  /** Кнопка правки сверху; у read-only сущностей (ВМ) — undefined. */
  edit?: { canEdit: boolean; onEdit: () => void };
  cards: HwCard[];
  diskTable?: HwDiskTable;
  footer?: ReactNode;
}

export function HardwareTab(props: Props) {
  // Карточка ВМ рендерится тем же файлом: у ВМ железо read-only, правка
  // ресурсов живёт во вкладке «Обзор», поэтому серверный edit-поток не нужен.
  // Диспетчер без хуков — модель ВМ собирается чистой функцией.
  if (props.entity?.kind === "vm") {
    return <HardwareView model={vmModel(props.entity.vm)} />;
  }
  return <ServerHardwareTab {...props} />;
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
                    <span key={iface} className="badge mono">
                      {iface}
                    </span>
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
 * (vCPU / RAM / сеть / системный диск). Правка ресурсов (vCPU/RAM) — во вкладке
 * «Обзор», дополнительные диски — во вкладке «Диски», поэтому здесь ни edit, ни
 * таблицы физдисков нет: те же карточки, read-only.
 */
function vmModel(vm: Vm): HardwareModel {
  const ramGb = (vm.ram_mb / 1024).toFixed(vm.ram_mb % 1024 === 0 ? 0 : 1);
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
    footer: (
      <>
        Дополнительные диски — во вкладке «Диски». Ресурсы (vCPU/RAM) правятся во
        вкладке «Обзор».
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
            <button
              className="btn btn-ghost flex items-center gap-1"
              onClick={model.edit.onEdit}
            >
              <Pencil className="w-4 h-4" /> Изменить
            </button>
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

      {model.diskTable && <DiskTableCard table={model.diskTable} />}

      {model.footer && <div className="text-[11px] text-dim">{model.footer}</div>}
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
                      <span className="badge badge-ok">system</span>
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
          Диски не редактируются вручную — backend заменяет весь массив
          целиком, реальное обновление идёт через inventory worker.
        </div>
      </div>
    </div>
  );
}
