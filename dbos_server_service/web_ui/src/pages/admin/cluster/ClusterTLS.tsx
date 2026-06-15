import { RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { isReadOnlyForCluster } from "@/lib/rbac";

export function ClusterTLS() {
  const { persona } = usePersona();
  const readonly = isReadOnlyForCluster(persona);
  const mockMode = useMockMode();
  return (
    <div className="space-y-4 max-w-7xl">
      {readonly && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · logging_reader.</b> Renew TLS недоступен — только
            просмотр.
          </span>
        </div>
      )}
      {!mockMode && (
        <div className="card">
          <h3 className="font-semibold flex items-center gap-2 mb-2">
            <ShieldCheck className="w-4 h-4 text-dim" /> TLS / сертификаты
          </h3>
          <div className="empty-card text-xs">
            Endpoint TLS-статуса ещё не подключён к UI.
          </div>
        </div>
      )}
      {mockMode && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-ok" /> TLS / сертификаты
            </h3>
            <span className="badge badge-ok">valid</span>
          </div>
          <div className="space-y-4">
            <div>
              <div className="text-xs text-dim">dbos-ingress-tls</div>
              <div className="text-base font-semibold">
                истекает через <span className="text-ok">87 дней</span>
              </div>
              <div className="text-[11px] text-dim mono">
                CN: dbos.astralinux.ru · SAN: *.dbos.astralinux.ru
              </div>
            </div>
            <div>
              <div className="text-xs text-dim">Internal CA</div>
              <div className="text-base font-semibold">
                истекает через <span className="text-ok">412 дней</span>
              </div>
              <div className="text-[11px] text-dim mono">
                CN: DBOS Internal CA · экспирация: 2027-07-26
              </div>
            </div>
            <div>
              <div className="text-xs text-dim">s2s mTLS (server↔secret)</div>
              <div className="text-base font-semibold">
                истекает через <span className="text-ok">203 дня</span>
              </div>
              <div className="text-[11px] text-dim mono">
                ротация: вместе с master-keys
              </div>
            </div>
          </div>
          {!readonly && (
            <div className="mt-4 flex items-center gap-2">
              <button className="btn btn-primary flex items-center gap-1">
                <RefreshCw className="w-3.5 h-3.5" /> Renew TLS only
              </button>
              <span className="text-[11px] text-dim mono">make k8s-tls-renew</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
