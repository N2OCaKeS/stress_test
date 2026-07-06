import {
  Activity,
  Cog,
  FileText,
  KeyRound,
  LockKeyhole,
  RefreshCw,
  ServerIcon,
} from "lucide-react";
import { CLUSTER_PODS } from "@/mocks/cluster";
import { pingCluster, type ServicePing } from "@/api/cluster/health";
import { useMockMode, useQuery } from "@/api/auth/useQuery";

const ICONS = {
  lock: LockKeyhole,
  server: ServerIcon,
  key: KeyRound,
  doc: FileText,
  cog: Cog,
};

function PingRow({ p }: { p: ServicePing }) {
  const Icon = ICONS[p.iconName];
  return (
    <div className="row-line">
      <div className="flex items-center gap-2">
        <Icon className="w-3.5 h-3.5 text-dim" /> {p.service}
      </div>
      <div className="flex items-center gap-3 text-xs">
        {!p.probeable ? (
          <span className="badge">{p.error ?? "нет HTTP-проба"}</span>
        ) : (
          <>
            {p.latency_ms != null && (
              <span className="mono text-dim">{p.latency_ms} мс</span>
            )}
            {p.ok ? (
              <span className="pod">
                <span className="health-led led-ok" /> up · HTTP {p.status}
              </span>
            ) : (
              <span className="pod text-danger">
                <span className="health-led led-danger" />{" "}
                down · {p.error ?? `HTTP ${p.status}`}
              </span>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export function ClusterHealth() {
  const mockMode = useMockMode();

  // pingCluster никогда не бросает — error-ветка useQuery здесь не сработает,
  // статус каждого сервиса несёт сама строка.
  const { data, loading, refetch } = useQuery<ServicePing[]>(
    pingCluster,
    [],
    { enabled: !mockMode, keepPreviousDataOnError: true },
  );

  return (
    <div className="space-y-4 w-full">
      {!mockMode && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold flex items-center gap-2">
              <Activity className="w-4 h-4 text-dim" /> Cluster health · прямой
              пинг
            </h3>
            <button
              className="btn flex items-center gap-1"
              onClick={refetch}
              disabled={loading}
            >
              <RefreshCw
                className={`w-3.5 h-3.5${loading ? " animate-spin" : ""}`}
              />{" "}
              Обновить
            </button>
          </div>
          {loading && !data ? (
            <div className="empty-card text-xs">Пинг сервисов…</div>
          ) : (
            <div className="space-y-2">
              {(data ?? []).map((p) => (
                <PingRow key={p.service} p={p} />
              ))}
            </div>
          )}
          <div className="text-[11px] text-dim mt-3 pt-3 border-t border-token">
            Прямой пинг <span className="mono">/health</span> каждого сервиса
            через общий прокси. <span className="mono">server_worker</span> без
            HTTP-API — heartbeat пишет в БД и из браузера не виден.
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
