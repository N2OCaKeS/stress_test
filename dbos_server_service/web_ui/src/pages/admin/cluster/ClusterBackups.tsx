import { Archive, CloudUpload, ShieldAlert } from "lucide-react";
import { BACKUPS } from "@/mocks/cluster";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { canMutateCluster, isReadOnlyForCluster } from "@/lib/rbac";

export function ClusterBackups() {
  const { persona } = usePersona();
  const readonly = isReadOnlyForCluster(persona);
  const canMutate = canMutateCluster(persona);
  const mockMode = useMockMode();
  return (
    <div className="space-y-4 max-w-3xl">
      {readonly && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · logging_reader.</b> Запуск manual backup недоступен.
          </span>
        </div>
      )}
      {!mockMode && (
        <div className="card">
          <h3 className="font-semibold flex items-center gap-2 mb-2">
            <Archive className="w-4 h-4 text-dim" /> Бэкапы / DR-drills
          </h3>
          <div className="empty-card text-xs">
            Endpoint бэкапов ещё не подключён к UI.
          </div>
        </div>
      )}
      {mockMode && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold flex items-center gap-2">
              <Archive className="w-4 h-4 text-accent" /> Бэкапы / DR-drills
            </h3>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-dim">Offsite</span>
              <span className="badge badge-ok">on</span>
            </div>
          </div>
          <div className="space-y-2">
            {BACKUPS.map((b) => (
              <div key={b.name} className="row-line">
                <div>
                  <div>{b.name}</div>
                  <div className="text-[11px] text-dim">{b.meta}</div>
                </div>
                <span className="badge badge-ok">{b.badge}</span>
              </div>
            ))}
          </div>
          {canMutate && (
            <button className="btn mt-3 w-full flex items-center justify-center gap-1">
              <CloudUpload className="w-3.5 h-3.5" /> Запустить manual backup
            </button>
          )}
          <div className="text-[11px] text-dim mt-3 pt-3 border-t border-token">
            DR-drill: ежемесячная репликация в isolated namespace + smoke-test.
          </div>
        </div>
      )}
    </div>
  );
}
