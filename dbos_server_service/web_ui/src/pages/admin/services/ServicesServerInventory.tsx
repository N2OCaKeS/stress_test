import { useState } from "react";
import { ServerIcon, Edit3, Power, Trash2 } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { SERVERS } from "@/mocks/server";
import { DEPTS } from "@/mocks/auth";
import { InlineEditor, FormRow, NotWiredInline, StatRow, useInlineState } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";
import { formatMsk } from "@/lib/datetime";

export function ServicesServerInventory() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="server_service"
        endpoints={[
          "GET    /server/v1/servers",
          "POST   /server/v1/servers",
          "PATCH  /server/v1/servers/{id}",
          "DELETE /server/v1/servers/{id}",
        ]}
      />
    );
  }
  const isAccountAdmin = persona.platform_role === "account_admin";
  const isDepAdmin = persona.platform_role === "dep_admin";
  const hasServerAdminRole = persona.service_roles?.server === "admin";
  const canEdit = isAccountAdmin || isDepAdmin || hasServerAdminRole;
  // dep scope for dep_admin / service-admin; cluster-wide for account_admin.
  const visible = isAccountAdmin
    ? SERVERS
    : persona.dept_id
      ? SERVERS.filter((s) => s.dept_id === persona.dept_id)
      : SERVERS;

  return (
    <InlineEditor
      title="Серверы · server_service"
      icon={ServerIcon}
      hint="инвентарь, питание, ребут, BMC · drift логируется как WARNING"
      items={visible}
      getId={(s) => s.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : undefined}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <ServerIcon className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.hostname}</div>
              <div className="text-[11px] text-dim truncate">
                {item.dept_id} · {item.ip}
              </div>
            </div>
            <span className={`badge badge-${item.status === "up" ? "ok" : item.status === "maintenance" ? "warn" : "danger"}`}>
              {item.status}
            </span>
          </div>
        </button>
      )}
      renderDetail={(s, { editing, onClose }) => {
        if (editing) return <ServerForm initial={s} onDone={onClose} mode="edit" />;
        return <ServerView server={s} canEdit={canEdit} />;
      }}
      renderCreate={canEdit ? (onClose) => <ServerForm onDone={onClose} mode="new" /> : undefined}
    />
  );
}

function ServerView({
  server,
  canEdit,
}: {
  server: typeof SERVERS[number];
  canEdit: boolean;
}) {
  const { startEdit } = useInlineState();
  return (
    <div className="card w-full">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2 mono">
          <ServerIcon className="w-4 h-4 text-accent" /> {server.hostname}
          <span className={`badge badge-${server.status === "up" ? "ok" : server.status === "maintenance" ? "warn" : "danger"}`}>
            {server.status}
          </span>
        </h3>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button className="btn flex items-center gap-1" onClick={() => startEdit(server.id)}>
              <Edit3 className="w-4 h-4" /> Изменить
            </button>
            <button className="btn flex items-center gap-1">
              <Power className="w-4 h-4" /> Перезагрузить питание
            </button>
            <button className="btn btn-danger flex items-center gap-1">
              <Trash2 className="w-4 h-4" /> Вывести из эксплуатации
            </button>
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-6">
        <div>
          <StatRow k="server_id" v={<span className="mono">{server.id}</span>} />
          <StatRow k="hostname" v={<span className="mono">{server.hostname}</span>} />
          <StatRow k="ip" v={<span className="mono">{server.ip}</span>} />
          <StatRow k="bmc_ip" v={<span className="mono">{server.bmc_ip}</span>} />
        </div>
        <div>
          <StatRow k="dept" v={server.dept_id} />
          <StatRow k="os" v={server.os} />
          <StatRow k="rack" v={<span className="mono">{server.rack}</span>} />
          <StatRow k="power" v={server.power_state} />
        </div>
      </div>
      <StatRow k="last_seen" v={<span className="mono">{formatMsk(server.last_seen)}</span>} />
    </div>
  );
}

function ServerForm({
  initial,
  onDone,
  mode,
}: {
  initial?: typeof SERVERS[number];
  onDone: () => void;
  mode: "new" | "edit";
}) {
  const [hostname, setHostname] = useState(initial?.hostname ?? "");
  const [ip, setIp] = useState(initial?.ip ?? "");
  const [bmcIp, setBmcIp] = useState(initial?.bmc_ip ?? "");
  const [dept, setDept] = useState(initial?.dept_id ?? DEPTS[0]?.id ?? "");
  const [os, setOs] = useState(initial?.os ?? "Astra Linux SE 1.8");
  const [rack, setRack] = useState(initial?.rack ?? "");

  return (
    <div className="card w-full">
      <h3 className="font-semibold mb-3 flex items-center gap-2">
        <ServerIcon className="w-4 h-4 text-accent" />
        {mode === "new" ? "Новый сервер" : `Изменить · ${initial?.hostname}`}
      </h3>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="hostname">
          <input className="input mono" value={hostname} onChange={(e) => setHostname(e.target.value)} />
        </FormRow>
        <FormRow label="dept">
          <select className="input" value={dept} onChange={(e) => setDept(e.target.value)}>
            {DEPTS.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </FormRow>
        <FormRow label="ip">
          <input className="input mono" value={ip} onChange={(e) => setIp(e.target.value)} />
        </FormRow>
        <FormRow label="bmc_ip">
          <input className="input mono" value={bmcIp} onChange={(e) => setBmcIp(e.target.value)} />
        </FormRow>
        <FormRow label="os">
          <input className="input" value={os} onChange={(e) => setOs(e.target.value)} />
        </FormRow>
        <FormRow label="rack">
          <input className="input mono" value={rack} onChange={(e) => setRack(e.target.value)} />
        </FormRow>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onDone}>Отмена</button>
        <button className="btn btn-primary" onClick={onDone}>
          {mode === "new" ? "Создать" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
