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
import { Cpu, HardDrive, MemoryStick, Network, Pencil } from "lucide-react";
import type { Server, ServerUpdateRequest } from "@/api/server/types";
import { updateServer } from "@/api/server/servers";
import { apiErrMsg } from "@/api/client";
import { usePersona } from "@/contexts/PersonaContext";
import { isDepAdmin } from "@/lib/rbac";
import { FormRow, StatRow } from "@/pages/admin/services/_inline";

interface Props {
  serverId: string;
  server?: Server;
  /** Поднимает свежий объект в ServerDetail, чтобы header и соседние
   * вкладки обновились после PATCH без перезагрузки страницы. */
  onServerUpdated?: (next: Server) => void;
}

export function HardwareTab({ server, onServerUpdated }: Props) {
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
      server={view}
      canEdit={canEdit}
      onEdit={() => setEditing(true)}
    />
  );
}

function HardwareView({
  server,
  canEdit,
  onEdit,
}: {
  server: Server;
  canEdit: boolean;
  onEdit: () => void;
}) {
  const ramGb =
    server.ram_total_mb != null
      ? (server.ram_total_mb / 1024).toFixed(server.ram_total_mb % 1024 === 0 ? 0 : 1)
      : null;

  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="flex justify-end">
        {canEdit && (
          <button
            className="btn btn-ghost flex items-center gap-1"
            onClick={onEdit}
          >
            <Pencil className="w-4 h-4" /> Редактировать
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
            <Cpu className="w-4 h-4 text-accent" /> CPU
          </h3>
          <StatRow
            k="brand"
            v={server.cpu_brand ?? <span className="text-dim">—</span>}
          />
          <StatRow
            k="model"
            v={
              server.cpu_model ? (
                <span className="mono">{server.cpu_model}</span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
          <StatRow
            k="cores"
            v={
              server.cpu_cores != null ? (
                <span className="mono">{server.cpu_cores}</span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
          <StatRow
            k="threads"
            v={
              server.cpu_threads != null ? (
                <span className="mono">{server.cpu_threads}</span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
          <StatRow
            k="frequency"
            v={
              server.cpu_frequency_ghz != null ? (
                <span className="mono">{server.cpu_frequency_ghz} GHz</span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
        </div>

        <div className="card">
          <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
            <MemoryStick className="w-4 h-4 text-accent" /> RAM
          </h3>
          <StatRow
            k="total_mb"
            v={
              server.ram_total_mb != null ? (
                <span className="mono">{server.ram_total_mb}</span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
          <StatRow
            k="total_gb"
            v={
              ramGb != null ? (
                <span className="mono">{ramGb} GB</span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
        </div>

        <div className="card">
          <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
            <Network className="w-4 h-4 text-accent" /> Сеть
          </h3>
          <StatRow
            k="interface_name"
            v={
              server.network_interface_name ? (
                <span className="mono">{server.network_interface_name}</span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
          <StatRow
            k="interfaces"
            v={
              server.network_interfaces && server.network_interfaces.length > 0 ? (
                <span className="flex flex-wrap gap-1">
                  {server.network_interfaces.map((iface) => (
                    <span key={iface} className="badge mono">
                      {iface}
                    </span>
                  ))}
                </span>
              ) : (
                <span className="text-dim">—</span>
              )
            }
          />
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
        </div>
      </div>

      <div className="card">
        <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
          <HardDrive className="w-4 h-4 text-accent" /> Диски
          <span className="text-xs text-dim font-normal">
            ({server.storage.length})
          </span>
        </h3>
        {server.storage.length === 0 ? (
          <div className="text-sm text-dim">Дисков в инвентаре нет.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-dim text-xs border-b border-token">
                  <th className="text-left py-2 pr-3">slot</th>
                  <th className="text-left py-2 pr-3">size, GB</th>
                  <th className="text-left py-2 pr-3">used, GB</th>
                  <th className="text-left py-2 pr-3">used, %</th>
                  <th className="text-left py-2 pr-3">model</th>
                  <th className="text-left py-2 pr-3">system</th>
                  <th className="text-left py-2 pr-3">id</th>
                </tr>
              </thead>
              <tbody>
                {server.storage.map((d) => (
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
        <div className="mt-3 text-[11px] text-dim">
          Редактирование дисков идёт через инвентаризацию (`inventory_sync`) —
          вручную сюда писать не нужно.
        </div>
      </div>
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
          <Pencil className="w-4 h-4 text-accent" /> Hardware ·{" "}
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
