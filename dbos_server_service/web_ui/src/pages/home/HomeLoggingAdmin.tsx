import {
  Activity,
  Search,
  ShieldAlert,
  History,
  AlertCircle,
  AlertTriangle,
  Clock,
  Lightbulb,
} from "lucide-react";
import { Link } from "react-router-dom";
import { HomeShell } from "./HomeShell";
import { usePersona } from "@/contexts/PersonaContext";
import { AUDIT_EVENTS } from "@/mocks/log";
import { useMockMode } from "@/api/auth/useQuery";

/**
 * Logging-admin Home.
 * Audit-channel admin: live feed + rules + retention.
 */
export function HomeLoggingAdmin() {
  const { persona } = usePersona();
  const mockMode = useMockMode();

  if (!mockMode) {
    return (
      <HomeShell
        title={<>Привет, {persona.username} 👋</>}
        subtitle={
          <>
            Доступ к audit-каналу платформы · loging_service дашборд ещё не
            подключён к UI
          </>
        }
      >
        <section className="mb-8">
          <h2 className="text-sm uppercase tracking-wider text-dim mb-3">
            Быстрые действия
          </h2>
          <div className="grid gap-3 md:grid-cols-4">
            <Link to="/log" className="quick-tile">
              <div className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Открыть лог</span>
              </div>
              <div className="text-xs text-dim">audit-канал</div>
            </Link>
            <Link to="/log?q=req_id" className="quick-tile">
              <div className="flex items-center gap-2">
                <Search className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Поиск по request_id</span>
              </div>
              <div className="text-xs text-dim">trace по платформе</div>
            </Link>
            <Link to="/admin" className="quick-tile">
              <div className="flex items-center gap-2">
                <ShieldAlert className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Правила</span>
              </div>
              <div className="text-xs text-dim">severity / brute-force</div>
            </Link>
            <Link to="/admin" className="quick-tile">
              <div className="flex items-center gap-2">
                <History className="w-5 h-5 text-warn" />
                <span className="text-sm font-medium">Retention</span>
              </div>
              <div className="text-xs text-dim">политики хранения</div>
            </Link>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2">
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">Audit overview</h3>
              <Link to="/log" className="text-xs text-accent">
                Открыть лог →
              </Link>
            </div>
            <div className="empty-card text-xs">
              Сводка событий ещё не подключена. Endpoint: GET /audit/summary.
            </div>
          </div>
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">Live feed</h3>
              <span className="text-xs text-dim">не подключено</span>
            </div>
            <div className="empty-card text-xs">
              Стрим аудита ещё не подключён к UI.
            </div>
          </div>
        </section>
      </HomeShell>
    );
  }

  // ---- mock-mode rendering ----
  const critical = AUDIT_EVENTS.filter((e) => e.severity === "critical").length;

  return (
    <HomeShell
      title={<>Привет, {persona.username} 👋</>}
      subtitle={
        <>
          Доступ к audit-каналу платформы · <b>read</b> + <b>rules</b> +{" "}
          <b>retention</b>
        </>
      }
    >
      {/* Quick actions */}
      <section className="mb-8">
        <h2 className="text-sm uppercase tracking-wider text-dim mb-3">
          Быстрые действия
        </h2>
        <div className="grid gap-3 md:grid-cols-4">
              <Link to="/log" className="quick-tile">
                <div className="flex items-center gap-2">
                  <Activity className="w-5 h-5 text-accent" />
                  <span className="text-sm font-medium">Live-фид</span>
                </div>
                <div className="text-xs text-dim">Открыть real-time stream</div>
              </Link>
              <Link to="/log?q=req_id" className="quick-tile">
                <div className="flex items-center gap-2">
                  <Search className="w-5 h-5 text-accent" />
                  <span className="text-sm font-medium">Поиск по request_id</span>
                </div>
                <div className="text-xs text-dim">Trace по платформе</div>
              </Link>
              <Link to="/admin#rules" className="quick-tile">
                <div className="flex items-center gap-2">
                  <ShieldAlert className="w-5 h-5 text-accent" />
                  <span className="text-sm font-medium">
                    Создать правило severity
                  </span>
                </div>
                <div className="text-xs text-dim">CRITICAL / WARN auto-rule</div>
              </Link>
              <Link to="/admin#retention" className="quick-tile">
                <div className="flex items-center gap-2">
                  <History className="w-5 h-5 text-warn" />
                  <span className="text-sm font-medium">Retention status</span>
                </div>
                <div className="text-xs text-dim">Sweep, политики, объёмы</div>
              </Link>
            </div>
          </section>

          {/* Stats row */}
          <section className="mb-8 grid gap-4 md:grid-cols-4">
            <div className="card">
              <div className="stat-label">Events 24ч</div>
              <div className="stat-big">
                {AUDIT_EVENTS.length.toLocaleString("ru-RU")}
              </div>
              <div className="text-xs text-dim mt-2">пик 11:00 → 1 124/час</div>
            </div>
            <div className="card">
              <div className="stat-label">CRITICAL за 24ч</div>
              <div className="stat-big text-danger">{critical}</div>
              <div className="text-xs text-dim mt-2">7 из auth-service</div>
            </div>
            <div className="card">
              <div className="stat-label">Rules active</div>
              <div className="stat-big text-ok">23</div>
              <div className="text-xs text-dim mt-2">2 muted</div>
            </div>
            <div className="card">
              <div className="stat-label">Last sweep</div>
              <div className="stat-big">03:17</div>
              <div className="text-xs text-dim mt-2">
                удалено 14 312 строк &gt; 90 дней
              </div>
            </div>
          </section>

          {/* Bottom two columns */}
          <section className="grid gap-4 md:grid-cols-2">
            {/* Pending */}
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold">Требует внимания</h3>
                <span className="text-xs text-dim">4 пункта</span>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>
                      Скачок CRITICAL в <span className="mono">auth-service</span>
                    </div>
                    <div className="text-xs text-dim">
                      7 событий за последний час · норма &lt;1
                    </div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <AlertTriangle className="w-4 h-4 text-warn shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>
                      Retention для <span className="mono">debug</span> до 7 дней
                    </div>
                    <div className="text-xs text-dim">
                      диск-квота &gt;80% · стоит подкрутить
                    </div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <ShieldAlert className="w-4 h-4 text-warn shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>
                      Rule <span className="mono">brute-force-detect</span> muted
                      dave
                    </div>
                    <div className="text-xs text-dim">проверить причину</div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <Clock className="w-4 h-4 text-dim shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>
                      Запрос экспорта от <b>bob</b>
                    </div>
                    <div className="text-xs text-dim">
                      CRITICAL за 7 дней → CSV
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Live audit feed */}
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold">Live audit-фид</h3>
                <span className="badge badge-ok">streaming</span>
              </div>
              <div className="text-sm">
                {LIVE_FEED.map((row) => (
                  <div key={row.req} className="activity-row">
                    <span className="text-xs text-dim mono">{row.ts}</span>
                    <div className="min-w-0">
                      <div className="truncate">
                        <b>{row.actor}</b>{" "}
                        <span className="text-dim">{row.action}</span>{" "}
                        <span className="mono">{row.target}</span>
                      </div>
                      <div className="text-[11px] text-dim mono truncate">
                        {row.req}
                      </div>
                    </div>
                    <span className={`badge badge-${row.badgeKind}`}>
                      {row.badge}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </section>

      {/* Tip footer */}
      <section className="mt-8 card flex items-center gap-3 text-sm">
        <Lightbulb className="w-5 h-5 text-warn shrink-0" />
        <div>
          <b>Совет:</b> правила severity лучше создавать из живого фида —
          выдели событие и нажми «правило из этого».
        </div>
      </section>
    </HomeShell>
  );
}

interface FeedRow {
  ts: string;
  actor: string;
  action: string;
  target: string;
  req: string;
  badge: string;
  badgeKind: "ok" | "warn" | "danger";
}

const LIVE_FEED: FeedRow[] = [
  { ts: "16:07", actor: "auth-service", action: "login", target: "alice", req: "req_a781...", badge: "INFO", badgeKind: "ok" },
  { ts: "16:06", actor: "secret-service", action: "reveal", target: "grafana-admin", req: "req_b912...", badge: "INFO", badgeKind: "ok" },
  { ts: "16:05", actor: "auth-service", action: "failed-login", target: "charlie", req: "req_22c8...", badge: "WARN", badgeKind: "warn" },
  { ts: "16:04", actor: "auth-service", action: "brute-force-detect", target: "", req: "req_22c9...", badge: "CRIT", badgeKind: "danger" },
  { ts: "16:02", actor: "server-worker", action: "ipmi-probe", target: "srv-node-17", req: "req_kx32...", badge: "WARN", badgeKind: "warn" },
  { ts: "16:01", actor: "server-service", action: "power-on", target: "srv-node-19", req: "req_ka13...", badge: "INFO", badgeKind: "ok" },
  { ts: "15:59", actor: "secret-service", action: "rotate", target: "vault-token-ci", req: "req_4ab1...", badge: "INFO", badgeKind: "ok" },
  { ts: "15:57", actor: "auth-service", action: "acl-update", target: "", req: "req_001f...", badge: "INFO", badgeKind: "ok" },
  { ts: "15:55", actor: "config-service", action: "cert renew", target: "edge-ca", req: "req_9b7c...", badge: "INFO", badgeKind: "ok" },
  { ts: "15:54", actor: "auth-service", action: "user banned", target: "charlie", req: "req_22d0...", badge: "CRIT", badgeKind: "danger" },
];
