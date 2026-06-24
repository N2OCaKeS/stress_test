import {
  Building2,
  UserPlus,
  FileText,
  Activity,
  AlertCircle,
  AlertTriangle,
  ShieldAlert,
  Clock,
  Lightbulb,
} from "lucide-react";
import { Link } from "react-router-dom";
import { HomeShell } from "./HomeShell";
import { usePersona } from "@/contexts/PersonaContext";
import { USERS, DEPTS } from "@/mocks/auth";
import { SERVERS } from "@/mocks/server";
import { CREDENTIALS } from "@/mocks/secret";
import { AUDIT_EVENTS } from "@/mocks/log";
import { listUsers } from "@/api/auth/users";
import { listDepartments } from "@/api/auth/departments";
import { listBots } from "@/api/auth/bots";
import { listGroups } from "@/api/auth/groups";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { RecentAuditEvents } from "./widgets/RecentAuditEvents";
import { ServicesHealth } from "./widgets/ServicesHealth";

/**
 * Account-admin Home.
 * Platform-wide view: depts, users, servers, creds aggregated.
 */
export function HomeAccountAdmin() {
  const { persona } = usePersona();
  const mockMode = useMockMode();

  // Live-mode counts from auth_service. server/secret/worker dashboards aren't
  // wired to UI yet — they render as placeholder tiles.
  const usersQ = useQuery(
    () => listUsers({ limit: 1, include_banned: true }),
    [],
    { enabled: !mockMode },
  );
  const deptsQ = useQuery(() => listDepartments(), [], { enabled: !mockMode });
  const botsQ = useQuery(() => listBots({ limit: 1 }), [], {
    enabled: !mockMode,
  });
  const groupsQ = useQuery(() => listGroups({ limit: 1 }), [], {
    enabled: !mockMode,
  });

  if (!mockMode) {
    const liveUsers = usersQ.data?.total ?? usersQ.data?.items?.length ?? 0;
    const liveDepts = deptsQ.data?.length ?? 0;
    const liveBots = botsQ.data?.length ?? 0;
    const liveGroups = groupsQ.data?.length ?? 0;
    const anyLoading =
      usersQ.loading || deptsQ.loading || botsQ.loading || groupsQ.loading;

    return (
      <HomeShell
        title={<>Привет, {persona.username} 👋</>}
        subtitle={
          <>
            Платформа целиком · auth_service подключён · server / secret /
            worker dashboards ещё не подключены к UI
          </>
        }
      >
        <section className="mb-8">
          <h2 className="text-sm uppercase tracking-wider text-dim mb-3">
            Быстрые действия
          </h2>
          <div className="grid gap-3 md:grid-cols-4">
            <Link to="/admin" className="quick-tile">
              <div className="flex items-center gap-2">
                <Building2 className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Управление платформой</span>
              </div>
              <div className="text-xs text-dim">depts · users · bots · roles</div>
            </Link>
            <Link to="/users" className="quick-tile">
              <div className="flex items-center gap-2">
                <UserPlus className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Пользователи</span>
              </div>
              <div className="text-xs text-dim">создать / отредактировать</div>
            </Link>
            <Link to="/log" className="quick-tile">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Аудит</span>
              </div>
              <div className="text-xs text-dim">loging_service</div>
            </Link>
            <Link to="/worker" className="quick-tile">
              <div className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-warn" />
                <span className="text-sm font-medium">Worker</span>
              </div>
              <div className="text-xs text-dim">server_worker</div>
            </Link>
          </div>
        </section>

        <section className="mb-8 grid gap-4 md:grid-cols-4">
          <div className="card">
            <div className="stat-label">Users</div>
            <div className="stat-big">{anyLoading ? "—" : liveUsers}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
          <div className="card">
            <div className="stat-label">Departments</div>
            <div className="stat-big">{anyLoading ? "—" : liveDepts}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
          <div className="card">
            <div className="stat-label">Bots</div>
            <div className="stat-big">{anyLoading ? "—" : liveBots}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
          <div className="card">
            <div className="stat-label">Groups</div>
            <div className="stat-big">{anyLoading ? "—" : liveGroups}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2">
          <RecentAuditEvents />
          <ServicesHealth />
        </section>
      </HomeShell>
    );
  }

  // ---- mock-mode rendering (used by VITE_USE_MOCK_AUTH=true) ----
  const critical = AUDIT_EVENTS.filter((e) => e.severity === "critical").length;
  const warning = AUDIT_EVENTS.filter((e) => e.severity === "warning").length;

  return (
    <HomeShell
      title={<>Привет, {persona.username} 👋</>}
      subtitle={
        <>
          Платформа целиком · <b>{DEPTS.length}</b> dept · <b>{USERS.length}</b>{" "}
          users · <b>{SERVERS.length}</b> servers ·{" "}
          <b>{CREDENTIALS.length}</b> credentials
        </>
      }
    >
      {/* Quick actions */}
      <section className="mb-8">
        <h2 className="text-sm uppercase tracking-wider text-dim mb-3">
          Быстрые действия
        </h2>
        <div className="grid gap-3 md:grid-cols-4">
          <Link to="/admin" className="quick-tile">
            <div className="flex items-center gap-2">
              <Building2 className="w-5 h-5 text-accent" />
              <span className="text-sm font-medium">Создать dept</span>
            </div>
            <div className="text-xs text-dim">Новый изолированный отдел</div>
          </Link>
          <Link to="/users" className="quick-tile">
            <div className="flex items-center gap-2">
              <UserPlus className="w-5 h-5 text-accent" />
              <span className="text-sm font-medium">Завести user</span>
            </div>
            <div className="text-xs text-dim">Любой деп · любая роль</div>
          </Link>
          <Link to="/log" className="quick-tile">
            <div className="flex items-center gap-2">
              <FileText className="w-5 h-5 text-accent" />
              <span className="text-sm font-medium">Аудит платформы</span>
            </div>
            <div className="text-xs text-dim">Cross-dept · полный канал</div>
          </Link>
          <Link to="/worker" className="quick-tile">
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-warn" />
              <span className="text-sm font-medium">Состояние кластера</span>
            </div>
            <div className="text-xs text-dim">k8s · workers · бэкапы</div>
          </Link>
        </div>
      </section>

      {/* Stats row */}
      <section className="mb-8 grid gap-4 md:grid-cols-4">
        <div className="card">
          <div className="stat-label">Cluster pods</div>
          <div className="stat-big text-ok">
            17 <span className="text-base text-dim">/ 17</span>
          </div>
          <div className="text-xs text-dim mt-2">все Running</div>
        </div>
        <div className="card">
          <div className="stat-label">Audit events 24ч</div>
          <div className="stat-big">
            {AUDIT_EVENTS.length.toLocaleString("ru-RU")}
          </div>
          <div className="text-xs text-dim mt-2">
            {critical} critical · {warning} warning
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Pending rotations</div>
          <div className="stat-big text-warn">5</div>
          <div className="text-xs text-dim mt-2">2 просрочены</div>
        </div>
        <div className="card">
          <div className="stat-label">Backups OK</div>
          <div className="stat-big text-ok">
            6 <span className="text-base text-dim">/ 6</span>
          </div>
          <div className="text-xs text-dim mt-2">последний 03:17</div>
        </div>
      </section>

      {/* Bottom two columns */}
      <section className="grid gap-4 md:grid-cols-2">
        {/* Pending actions */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Требует внимания</h3>
            <span className="text-xs text-dim">5 пунктов</span>
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>
                  Pod <span className="mono">auth-service-2</span> CrashLoop
                </div>
                <div className="text-xs text-dim">с 13:54 · 7 рестартов</div>
              </div>
            </div>
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <AlertTriangle className="w-4 h-4 text-warn shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>2 ротации просрочены в депе <b>ДТКК</b></div>
                <div className="text-xs text-dim">deadline истёк 2 дня назад</div>
              </div>
            </div>
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <ShieldAlert className="w-4 h-4 text-warn shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>
                  Запрос на новый dept от <span className="mono">igor</span>
                </div>
                <div className="text-xs text-dim">
                  «QA-Stenders» · ждёт твоего approve
                </div>
              </div>
            </div>
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <AlertTriangle className="w-4 h-4 text-warn shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>Disk usage worker-node-04 → 87%</div>
                <div className="text-xs text-dim">логи postgres переполняются</div>
              </div>
            </div>
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <Clock className="w-4 h-4 text-dim shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>
                  Migration <span className="mono">0024_aes_to_keyset</span>
                </div>
                <div className="text-xs text-dim">
                  12 / {CREDENTIALS.length} credentials мигрированы
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Platform activity */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Активность платформы</h3>
            <Link to="/log" className="text-xs text-accent">
              Открыть полный лог →
            </Link>
          </div>
          <div className="text-sm">
            {PLATFORM_ACTIVITY.map((row) => (
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
          <b>Совет:</b> ты единственный, кто видит платформу целиком —
          используй <span className="mono surface-2 px-1 rounded">⌘K</span>{" "}
          для поиска по всем департаментам сразу.
        </div>
      </section>
    </HomeShell>
  );
}

interface ActivityRow {
  ts: string;
  actor: string;
  action: string;
  target: string;
  req: string;
  badge: string;
  badgeKind: "ok" | "warn" | "danger";
}

const PLATFORM_ACTIVITY: ActivityRow[] = [
  { ts: "16:04", actor: "bob", action: "создал dept", target: "qa-stenders", req: "req_9b21...", badge: "success", badgeKind: "ok" },
  { ts: "15:42", actor: "alice", action: "завела user", target: "igor", req: "req_7e9f...", badge: "success", badgeKind: "ok" },
  { ts: "14:18", actor: "worker_bot", action: "ротация", target: "vault-token-ci", req: "req_4ab1...", badge: "success", badgeKind: "ok" },
  { ts: "13:54", actor: "k8s", action: "restart pod", target: "auth-service-2", req: "req_kx12...", badge: "crash", badgeKind: "danger" },
  { ts: "12:01", actor: "cron", action: "backup snapshot", target: "pg-master", req: "job_b0c1...", badge: "done", badgeKind: "ok" },
  { ts: "10:38", actor: "pavel", action: "добавил server", target: "srv-edge-19", req: "req_fa10...", badge: "success", badgeKind: "ok" },
];
