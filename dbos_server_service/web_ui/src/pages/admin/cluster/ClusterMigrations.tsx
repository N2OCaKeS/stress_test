import { Database, ShieldAlert } from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { isReadOnlyForCluster } from "@/lib/rbac";

export function ClusterMigrations() {
  const { persona } = usePersona();
  const readonly = isReadOnlyForCluster(persona);
  const mockMode = useMockMode();
  return (
    <div className="space-y-4 max-w-3xl">
      {readonly && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · logging_reader.</b> Force-finalize миграций
            недоступен — только просмотр прогресса.
          </span>
        </div>
      )}
      {!mockMode && (
        <div className="card">
          <h3 className="font-semibold flex items-center gap-2 mb-2">
            <Database className="w-4 h-4 text-dim" /> Миграции · lazy re-encrypt
          </h3>
          <div className="empty-card text-xs">
            Endpoint миграций ещё не подключён к UI.
          </div>
        </div>
      )}
      {mockMode && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold flex items-center gap-2">
              <Database className="w-4 h-4 text-accent" /> Миграции · lazy re-encrypt
            </h3>
            <span className="badge">auto · 6мес</span>
          </div>
          <div className="mb-3">
            <div className="flex items-center justify-between text-xs mb-1">
              <span>server_service · lazy + outbox</span>
              <span className="text-ok font-semibold">78%</span>
            </div>
            <div className="bar ok">
              <span style={{ width: "78%" }} />
            </div>
            <div className="text-[11px] text-dim mt-1">
              legacy version: <span className="mono">v3</span> · 312 rows
            </div>
          </div>
          <div className="mb-3">
            <div className="flex items-center justify-between text-xs mb-1">
              <span>secret_service · lazy + outbox</span>
              <span className="text-warn font-semibold">52%</span>
            </div>
            <div className="bar warn">
              <span style={{ width: "52%" }} />
            </div>
            <div className="text-[11px] text-dim mt-1">
              legacy version: <span className="mono">v2</span> · 1 047 rows
            </div>
          </div>
          <div className="text-[11px] text-dim mt-3 pt-3 border-t border-token">
            Auto-finalize: <b>через 6 месяцев</b> · принудительное
            дошифрование оставшегося legacy.
          </div>
        </div>
      )}
    </div>
  );
}
