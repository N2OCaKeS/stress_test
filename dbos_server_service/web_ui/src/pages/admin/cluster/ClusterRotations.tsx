import { RotateCw, ShieldAlert } from "lucide-react";
import { ROTATIONS } from "@/mocks/cluster";
import { usePersona } from "@/contexts/PersonaContext";
import { useMockMode } from "@/api/auth/useQuery";
import { canMutateCluster, isReadOnlyForCluster } from "@/lib/rbac";

export function ClusterRotations() {
  const { persona } = usePersona();
  const readonly = isReadOnlyForCluster(persona);
  const canMutate = canMutateCluster(persona);
  const mockMode = useMockMode();
  return (
    <div className="space-y-4 max-w-7xl">
      {readonly && (
        <div className="readonly-bar">
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>
            <b>Read-only · logging_reader.</b> Триггер ротации недоступен —
            только просмотр расписания.
          </span>
        </div>
      )}
      {!mockMode && (
        <div className="card">
          <h3 className="font-semibold flex items-center gap-2 mb-2">
            <RotateCw className="w-4 h-4 text-dim" /> Master keys / ротации
          </h3>
          <div className="empty-card text-xs">
            Endpoint ротаций ещё не подключён к UI.
          </div>
        </div>
      )}
      {mockMode && (
        <>
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold flex items-center gap-2">
                <RotateCw className="w-4 h-4 text-accent" /> Master keys / ротации
              </h3>
              <span className="text-xs text-dim">{ROTATIONS.length} видов</span>
            </div>
            <div className="space-y-2">
              {ROTATIONS.map((r) => (
                <div key={r.name} className="row-line">
                  <div>
                    <div className="mono text-xs">{r.name}</div>
                    <div className="text-[11px] text-dim">
                      next: {r.next} · ok: {r.last}
                    </div>
                  </div>
                  {canMutate && <button className="btn">Trigger</button>}
                </div>
              ))}
            </div>
            <div className="mt-3 pt-3 border-t border-token text-[11px] text-dim">
              Master-keys ротация — операция account-уровня; запуск создаёт audit-event
              <span className="mono"> rotation.master.start</span>.
            </div>
          </div>

          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold flex items-center gap-2">
                secret-master · последняя ротация
              </h3>
              <span className="badge badge-ok">scheduled</span>
            </div>
            <div className="space-y-3">
              <div>
                <div className="text-xs text-dim">last success</div>
                <div className="text-base font-semibold">
                  <span className="text-ok">05.06 03:00</span> · keyset v3 · 18 sec
                </div>
              </div>
              <div>
                <div className="text-xs text-dim">next run</div>
                <div className="text-base font-semibold">
                  12.06 03:00{" "}
                  <span className="text-dim text-xs">(через 2 дня)</span>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
