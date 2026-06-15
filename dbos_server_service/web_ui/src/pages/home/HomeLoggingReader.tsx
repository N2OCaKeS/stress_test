import { Eye, Lock, Search, Filter, Lightbulb } from "lucide-react";
import { Link } from "react-router-dom";
import { HomeShell } from "./HomeShell";
import { usePersona } from "@/contexts/PersonaContext";
import { AUDIT_EVENTS } from "@/mocks/log";
import { useMockMode } from "@/api/auth/useQuery";
import { RecentAuditEvents } from "./widgets/RecentAuditEvents";

/**
 * Logging-reader Home.
 * Read-only audit view: feed + search, no rules / retention / export.
 */
export function HomeLoggingReader() {
  const { persona } = usePersona();
  const mockMode = useMockMode();

  if (!mockMode) {
    return (
      <HomeShell
        title={<>Привет, {persona.username} 👋</>}
        subtitle={
          <>
            Read-only доступ к audit-каналу. Просмотр и поиск, без правил и
            retention.
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
                <Eye className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Открыть лог</span>
              </div>
              <div className="text-xs text-dim">audit read-only</div>
            </Link>
            <div className="quick-tile opacity-60 cursor-not-allowed">
              <div className="flex items-center gap-2">
                <Lock className="w-5 h-5 text-dim" />
                <span className="text-sm font-medium">Правила severity</span>
              </div>
              <div className="text-xs text-dim">Только loging_admin</div>
            </div>
            <div className="quick-tile opacity-60 cursor-not-allowed">
              <div className="flex items-center gap-2">
                <Lock className="w-5 h-5 text-dim" />
                <span className="text-sm font-medium">Retention</span>
              </div>
              <div className="text-xs text-dim">Только loging_admin</div>
            </div>
            <div className="quick-tile opacity-60 cursor-not-allowed">
              <div className="flex items-center gap-2">
                <Lock className="w-5 h-5 text-dim" />
                <span className="text-sm font-medium">Экспорт</span>
              </div>
              <div className="text-xs text-dim">Только loging_admin</div>
            </div>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2">
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">Что доступно</h3>
              <span className="badge">read-only</span>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex items-start gap-3 p-2 surface-2 rounded">
                <Eye className="w-4 h-4 text-ok shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div>Просмотр live-фида</div>
                  <div className="text-xs text-dim">все события платформы</div>
                </div>
              </div>
              <div className="flex items-start gap-3 p-2 surface-2 rounded">
                <Search className="w-4 h-4 text-ok shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div>Поиск по полям event&apos;а</div>
                  <div className="text-xs text-dim">
                    request_id · actor · severity · сервис
                  </div>
                </div>
              </div>
              <div className="flex items-start gap-3 p-2 surface-2 rounded">
                <Filter className="w-4 h-4 text-ok shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div>Локальные фильтры</div>
                  <div className="text-xs text-dim">
                    не сохраняются в платформу
                  </div>
                </div>
              </div>
              <div className="flex items-start gap-3 p-2 surface-2 rounded">
                <Lock className="w-4 h-4 text-dim shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div>Без правил, retention, экспорта</div>
                  <div className="text-xs text-dim">нужна роль loging_admin</div>
                </div>
              </div>
            </div>
          </div>
          <RecentAuditEvents />
        </section>
      </HomeShell>
    );
  }

  // ---- mock-mode rendering ----
  return (
    <HomeShell
      title={<>Привет, {persona.username} 👋</>}
      subtitle={
        <>
          Read-only доступ к audit-каналу. Просмотр и поиск, без правил и
          retention.
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
                  <Eye className="w-5 h-5 text-accent" />
                  <span className="text-sm font-medium">Открыть фид</span>
                </div>
                <div className="text-xs text-dim">Поток событий read-only</div>
              </Link>
              <div className="quick-tile opacity-60 cursor-not-allowed">
                <div className="flex items-center gap-2">
                  <Lock className="w-5 h-5 text-dim" />
                  <span className="text-sm font-medium">Правила severity</span>
                </div>
                <div className="text-xs text-dim">Только loging_admin</div>
              </div>
              <div className="quick-tile opacity-60 cursor-not-allowed">
                <div className="flex items-center gap-2">
                  <Lock className="w-5 h-5 text-dim" />
                  <span className="text-sm font-medium">Retention</span>
                </div>
                <div className="text-xs text-dim">Только loging_admin</div>
              </div>
              <div className="quick-tile opacity-60 cursor-not-allowed">
                <div className="flex items-center gap-2">
                  <Lock className="w-5 h-5 text-dim" />
                  <span className="text-sm font-medium">Экспорт</span>
                </div>
                <div className="text-xs text-dim">Только loging_admin</div>
              </div>
            </div>
          </section>

          {/* Stats row */}
          <section className="mb-8 grid gap-4 md:grid-cols-2">
            <div className="card">
              <div className="stat-label">Events 24ч</div>
              <div className="stat-big">
                {AUDIT_EVENTS.length.toLocaleString("ru-RU")}
              </div>
              <div className="text-xs text-dim mt-2">по всей платформе</div>
            </div>
            <div className="card">
              <div className="stat-label">Last critical</div>
              <div className="stat-big text-danger">16:04</div>
              <div className="text-xs text-dim mt-2">
                <span className="mono">auth-service</span> · brute-force-detect
              </div>
            </div>
          </section>

          {/* Bottom two columns */}
          <section className="grid gap-4 md:grid-cols-2">
            {/* What is accessible */}
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold">Что доступно</h3>
                <span className="badge">read-only</span>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <Eye className="w-4 h-4 text-ok shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>Просмотр live-фида</div>
                    <div className="text-xs text-dim">все события платформы</div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <Search className="w-4 h-4 text-ok shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>Поиск по полям event&apos;а</div>
                    <div className="text-xs text-dim">
                      request_id · actor · severity · сервис
                    </div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <Filter className="w-4 h-4 text-ok shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>Локальные фильтры</div>
                    <div className="text-xs text-dim">
                      не сохраняются в платформу
                    </div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-2 surface-2 rounded">
                  <Lock className="w-4 h-4 text-dim shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div>Без правил, retention, экспорта</div>
                    <div className="text-xs text-dim">нужна роль loging_admin</div>
                  </div>
                </div>
              </div>
            </div>

            {/* Last 10 events */}
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold">Последние 10 событий</h3>
                <Link to="/log" className="text-xs text-accent">
                  Открыть фид →
                </Link>
              </div>
              <div className="text-sm">
                {RECENT_EVENTS.map((row) => (
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
          <b>Совет:</b> request_id из RC-тикета можно вставить в поиск —
          увидишь полный путь запроса через все сервисы.
        </div>
      </section>
    </HomeShell>
  );
}

interface EventRow {
  ts: string;
  actor: string;
  action: string;
  target: string;
  req: string;
  badge: string;
  badgeKind: "ok" | "warn" | "danger";
}

const RECENT_EVENTS: EventRow[] = [
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
