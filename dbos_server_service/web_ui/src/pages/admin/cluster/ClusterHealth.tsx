import {
  Activity,
  Cog,
  FileText,
  KeyRound,
  LockKeyhole,
  ServerIcon,
  ShieldAlert,
} from "lucide-react";
import { CLUSTER_PODS } from "@/mocks/cluster";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { isReadOnlyForCluster } from "@/lib/rbac";

const ICONS = {
  lock: LockKeyhole,
  server: ServerIcon,
  key: KeyRound,
  doc: FileText,
  cog: Cog,
};

export function ClusterHealth() {
  const { persona } = usePersona();
  const readonly = isReadOnlyForCluster(persona);
  const mockMode = useMockMode();
  return (
    <div className="space-y-4 max-w-3xl">
      {readonly && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · logging_reader.</b> Доступен только просмотр статуса
            кластера.
          </span>
        </div>
      )}
      {!mockMode && (
        <div className="card">
          <h3 className="font-semibold flex items-center gap-2 mb-2">
            <Activity className="w-4 h-4 text-dim" /> Cluster health
          </h3>
          <div className="empty-card text-xs">
            Endpoint <span className="mono">/api/cluster/health</span> ещё не
            подключён.
          </div>
        </div>
      )}
      {mockMode && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold flex items-center gap-2">
              <Activity className="w-4 h-4 text-ok" /> Cluster health
            </h3>
            <span className="badge badge-ok">healthy</span>
          </div>
          <div className="flex items-center gap-3 mb-3">
            <span className="health-led led-ok" />
            <div className="text-2xl font-bold text-ok">All green</div>
          </div>
          <div className="space-y-2">
            {CLUSTER_PODS.map((p) => {
              const Icon = ICONS[p.iconName];
              return (
                <div key={p.svc} className="row-line">
                  <div className="flex items-center gap-2">
                    <Icon className="w-3.5 h-3.5 text-dim" /> {p.svc}
                  </div>
                  <span className="pod">
                    <span className="health-led led-ok" /> {p.ratio}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="mt-3 pt-3 border-t border-token grid grid-cols-3 gap-2 text-center text-xs">
            <div>
              <div className="text-dim">CPU avg</div>
              <div className="text-base font-semibold">27%</div>
            </div>
            <div>
              <div className="text-dim">Mem avg</div>
              <div className="text-base font-semibold">41%</div>
            </div>
            <div>
              <div className="text-dim">Uptime</div>
              <div className="text-base font-semibold">17д</div>
            </div>
          </div>
          <div className="text-[11px] text-dim mt-3">
            Последний рестарт: <span className="mono">server_worker</span> ·
            17 дней назад
          </div>
        </div>
      )}
    </div>
  );
}
