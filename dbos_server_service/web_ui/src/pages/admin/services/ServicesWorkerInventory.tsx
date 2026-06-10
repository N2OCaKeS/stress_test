import { Cog, Power } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { WORKER_PODS } from "@/mocks/cluster";
import { InlineEditor, NotWiredInline, StatRow } from "./_inline";
import { useMockMode } from "@/api/auth/useQuery";

export function ServicesWorkerInventory() {
  const mockMode = useMockMode();
  const { persona } = usePersona();
  if (!mockMode) {
    return (
      <NotWiredInline
        service="server_worker (pods)"
        endpoints={[
          "GET  /worker/v1/pods",
          "POST /worker/v1/pods/{id}/drain",
          "POST /worker/v1/pods/{id}/restart",
        ]}
      />
    );
  }
  const canEdit =
    persona.platform_role === "account_admin" ||
    persona.service_roles?.worker === "admin";

  return (
    <InlineEditor
      title="Воркеры · server_worker"
      icon={Cog}
      hint="инвентарь подов · drain / restart · CRUD = K8s deployment"
      items={WORKER_PODS}
      getId={(w) => w.id}
      canEdit={canEdit}
      readonlyNote={!canEdit ? "Просмотр без права изменения" : "Деплой и масштабирование — через K8s, здесь — операционные действия"}
      renderRow={({ item, active, onSelect }) => (
        <button className={`cred-row text-left ${active ? "active" : ""}`} onClick={onSelect}>
          <div className="flex items-center gap-2">
            <Cog className="w-4 h-4 text-dim" />
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate mono">{item.id}</div>
              <div className="text-[11px] text-dim truncate">{item.host} · uptime {item.uptime}</div>
            </div>
            <span className={`badge badge-${item.status === "running" ? "ok" : item.status === "draining" ? "warn" : ""}`}>
              {item.status}
            </span>
          </div>
        </button>
      )}
      renderDetail={(w) => (
        <div className="card max-w-2xl">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h3 className="font-semibold flex items-center gap-2 mono">
              <Cog className="w-4 h-4 text-accent" /> {w.id}
              <span className={`badge badge-${w.status === "running" ? "ok" : w.status === "draining" ? "warn" : ""}`}>
                {w.status}
              </span>
            </h3>
            {canEdit && (
              <div className="flex items-center gap-2">
                <button className="btn flex items-center gap-1">
                  <Power className="w-4 h-4" /> Drain
                </button>
                <button className="btn">Restart</button>
              </div>
            )}
          </div>
          <StatRow k="pod_id" v={<span className="mono">{w.id}</span>} />
          <StatRow k="host" v={<span className="mono">{w.host}</span>} />
          <StatRow k="status" v={w.status} />
          <StatRow k="in_flight" v={<span className="mono">{w.tasks_in_flight}</span>} />
          <StatRow k="uptime" v={<span className="mono">{w.uptime}</span>} />
          <StatRow k="last_task" v={<span className="mono">{w.last_task}</span>} />
        </div>
      )}
    />
  );
}
