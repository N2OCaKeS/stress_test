import {
  UserPlus,
  ServerCog,
  KeyRound,
  Activity,
  AlertTriangle,
  AlertCircle,
  ShieldAlert,
  Clock,
  Lightbulb,
} from "lucide-react";
import { Link } from "react-router-dom";
import { HomeShell } from "./HomeShell";
import { usePersona } from "@/contexts/PersonaContext";
import { deptDisplayName, personaDeptId } from "@/lib/rbac";
import { USERS } from "@/mocks/auth";
import { SERVERS } from "@/mocks/server";
import { CREDENTIALS } from "@/mocks/secret";
import { TASKS } from "@/mocks/worker";
import { AUDIT_EVENTS } from "@/mocks/log";
import { listUsers, listUsersByDepartment } from "@/api/auth/users";
import { listGroups, listGroupsByDepartment } from "@/api/auth/groups";
import { listBots } from "@/api/auth/bots";
import { useMockMode, useQuery } from "@/api/auth/useQuery";

/**
 * Port of home-dep_admin.html (alice).
 * Welcome → Quick tiles → Stats → 2-column bottom row → Tip footer.
 */
export function HomeDepAdmin() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const myDeptId = personaDeptId(persona);

  // Live counts inside dep_admin's scope. server/secret/worker not wired yet.
  const usersQ = useQuery(
    () =>
      myDeptId
        ? listUsersByDepartment(myDeptId, { limit: 1, include_banned: true })
        : listUsers({ limit: 1, include_banned: true }),
    [myDeptId],
    { enabled: !mockMode },
  );
  const groupsQ = useQuery(
    () => listGroups({ limit: 1 }),
    [],
    { enabled: !mockMode },
  );
  const botsQ = useQuery(
    () => listBots({ limit: 1 }),
    [],
    { enabled: !mockMode },
  );
  // Группы моего отдела — короткий list для dashboard-карточки.
  // Если у persona нет dept_id (например, account_admin без депа), пропускаем.
  const deptGroupsQ = useQuery(
    () =>
      myDeptId
        ? listGroupsByDepartment(myDeptId, { limit: 10 })
        : Promise.resolve([]),
    [myDeptId],
    { enabled: !mockMode && !!myDeptId },
  );

  if (!mockMode) {
    const liveUsers = usersQ.data?.total ?? usersQ.data?.items?.length ?? 0;
    const liveGroups = groupsQ.data?.length ?? 0;
    const liveBots = botsQ.data?.length ?? 0;
    const anyLoading = usersQ.loading || groupsQ.loading || botsQ.loading;

    return (
      <HomeShell
        title={<>Привет, {persona.username} 👋</>}
        subtitle={
          persona.dept_id ? (
            <>Departament <b>{deptDisplayName(persona.dept_id)}</b></>
          ) : (
            <>Платформа</>
          )
        }
      >
        <section className="mb-8">
          <h2 className="text-sm uppercase tracking-wider text-dim mb-3">
            Быстрые действия
          </h2>
          <div className="grid gap-3 md:grid-cols-4">
            <Link to="/users" className="quick-tile">
              <div className="flex items-center gap-2">
                <UserPlus className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Пользователи</span>
              </div>
              <div className="text-xs text-dim">создать / отредактировать</div>
            </Link>
            <Link to="/server" className="quick-tile">
              <div className="flex items-center gap-2">
                <ServerCog className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Серверы</span>
              </div>
              <div className="text-xs text-dim">server_service (не подключён)</div>
            </Link>
            <Link to="/secret" className="quick-tile">
              <div className="flex items-center gap-2">
                <KeyRound className="w-5 h-5 text-accent" />
                <span className="text-sm font-medium">Секреты</span>
              </div>
              <div className="text-xs text-dim">secret_service (не подключён)</div>
            </Link>
            <Link to="/admin" className="quick-tile">
              <div className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-warn" />
                <span className="text-sm font-medium">Админка</span>
              </div>
              <div className="text-xs text-dim">аудит, роли, политики</div>
            </Link>
          </div>
        </section>

        <section className="mb-8 grid gap-4 md:grid-cols-3">
          <div className="card">
            <div className="stat-label">Users (scope)</div>
            <div className="stat-big">{anyLoading ? "—" : liveUsers}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
          <div className="card">
            <div className="stat-label">Groups</div>
            <div className="stat-big">{anyLoading ? "—" : liveGroups}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
          <div className="card">
            <div className="stat-label">Bots</div>
            <div className="stat-big">{anyLoading ? "—" : liveBots}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2">
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">Группы моего отдела</h3>
              <Link to="/users" className="text-xs text-accent">
                Все →
              </Link>
            </div>
            {!myDeptId ? (
              <div className="empty-card text-xs">
                Persona без dept_id — фильтр по отделу неприменим.
              </div>
            ) : deptGroupsQ.loading ? (
              <div className="text-xs text-dim">Загрузка…</div>
            ) : deptGroupsQ.error ? (
              <div className="alert-danger">{deptGroupsQ.error.message}</div>
            ) : (deptGroupsQ.data ?? []).length === 0 ? (
              <div className="empty-card text-xs">
                В отделе пока нет групп.
              </div>
            ) : (
              <ul className="text-sm divide-y divide-token">
                {(deptGroupsQ.data ?? []).slice(0, 8).map((g) => (
                  <li
                    key={g.id}
                    className="py-1.5 flex items-center gap-2 min-w-0"
                  >
                    <Link
                      to={`/users/group/${g.id}`}
                      className="font-medium truncate hover-bg"
                    >
                      {g.name}
                    </Link>
                    <span
                      className="text-xs text-dim ml-auto truncate max-w-[160px]"
                      title={g.description ?? ""}
                    >
                      {g.description ?? ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">Активность</h3>
              <Link to="/log" className="text-xs text-accent">
                Открыть лог →
              </Link>
            </div>
            <div className="empty-card text-xs">
              Сводка audit-канала ещё не подключена.
            </div>
          </div>
        </section>
      </HomeShell>
    );
  }

  // ---- mock-mode rendering ----
  const usersInScope = myDeptId
    ? USERS.filter((u) => u.dept_id === myDeptId)
    : USERS;
  const serversInScope = myDeptId
    ? SERVERS.filter((s) => s.dept_id === myDeptId)
    : SERVERS;
  const credsInScope = myDeptId
    ? CREDENTIALS.filter((c) => c.dept_id === myDeptId)
    : CREDENTIALS;

  const serversUp = serversInScope.filter((s) => s.status === "up").length;
  const serversMaint = serversInScope.filter((s) => s.status === "maintenance").length;
  const failed = TASKS.filter((t) => t.state === "failed").length;
  const retry = TASKS.filter((t) => t.state === "retry").length;
  const critical = AUDIT_EVENTS.filter((e) => e.severity === "critical").length;
  const warning = AUDIT_EVENTS.filter((e) => e.severity === "warning").length;
  const expiring = credsInScope.filter((c) => c.status === "expiring").length;

  return (
    <HomeShell
      title={<>Привет, {persona.username} 👋</>}
      subtitle={
        persona.dept_id ? (
          <>
            Departament <b>{deptDisplayName(persona.dept_id)}</b> · {usersInScope.length}{" "}
            пользователей · {serversInScope.length} серверов ·{" "}
            {credsInScope.length} доступных credential&apos;ов
          </>
        ) : (
          <>
            Платформа · {USERS.length} пользователей · {SERVERS.length}{" "}
            серверов · {CREDENTIALS.length} credential&apos;ов
          </>
        )
      }
    >
      {/* Quick actions */}
      <section className="mb-8">
        <h2 className="text-sm uppercase tracking-wider text-dim mb-3">
          Быстрые действия
        </h2>
        <div className="grid gap-3 md:grid-cols-4">
          <Link to="/users" className="quick-tile">
            <div className="flex items-center gap-2">
              <UserPlus className="w-5 h-5 text-accent" />
              <span className="text-sm font-medium">Завести пользователя</span>
            </div>
            <div className="text-xs text-dim">
              В тебе уже {usersInScope.length} → +1
            </div>
          </Link>
          <Link to="/server" className="quick-tile">
            <div className="flex items-center gap-2">
              <ServerCog className="w-5 h-5 text-accent" />
              <span className="text-sm font-medium">Добавить сервер</span>
            </div>
            <div className="text-xs text-dim">IPMI + аккаунт</div>
          </Link>
          <Link to="/secret" className="quick-tile">
            <div className="flex items-center gap-2">
              <KeyRound className="w-5 h-5 text-accent" />
              <span className="text-sm font-medium">Создать credential</span>
            </div>
            <div className="text-xs text-dim">Token / password / key</div>
          </Link>
          <Link to="/admin" className="quick-tile">
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-warn" />
              <span className="text-sm font-medium">Состояние депа</span>
            </div>
            <div className="text-xs text-dim">Аудит, ротации, бэкап</div>
          </Link>
        </div>
      </section>

      {/* Stats row */}
      <section className="mb-8 grid gap-4 md:grid-cols-4">
        <div className="card">
          <div className="stat-label">Servers Up</div>
          <div className="stat-big text-ok">
            {serversUp}{" "}
            <span className="text-base text-dim">
              / {serversInScope.length}
            </span>
          </div>
          <div className="text-xs text-dim mt-2">
            {serversMaint} в обслуживании
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Tasks за 24ч</div>
          <div className="stat-big">{TASKS.length * 3 - 3}</div>
          <div className="text-xs text-dim mt-2">
            {failed} failed · {retry} retry
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Audit events</div>
          <div className="stat-big">
            {AUDIT_EVENTS.length.toLocaleString("ru-RU")}
          </div>
          <div className="text-xs text-dim mt-2">
            {critical} critical · {warning} warning
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Credentials</div>
          <div className="stat-big">{credsInScope.length}</div>
          <div className="text-xs text-dim mt-2">
            {expiring} истекает на этой неделе
          </div>
        </div>
      </section>

      {/* Bottom two columns */}
      <section className="grid gap-4 md:grid-cols-2">
        {/* Pending actions */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Требует внимания</h3>
            <span className="text-xs text-dim">4 пункта</span>
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <AlertTriangle className="w-4 h-4 text-warn shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>
                  Истекает credential{" "}
                  <span className="mono">grafana-admin</span>
                </div>
                <div className="text-xs text-dim">
                  через 3 дня · нажмите чтобы продлить
                </div>
              </div>
            </div>
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>
                  Server <span className="mono">srv-node-17</span> — IPMI
                  unreachable
                </div>
                <div className="text-xs text-dim">
                  с 14:22 · 2 ретрая в очереди
                </div>
              </div>
            </div>
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <ShieldAlert className="w-4 h-4 text-warn shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>
                  User <span className="mono">charlie</span> заблокирован
                </div>
                <div className="text-xs text-dim">
                  5 failed login за 10 мин · ручной разбор
                </div>
              </div>
            </div>
            <div className="flex items-start gap-3 p-2 surface-2 rounded">
              <Clock className="w-4 h-4 text-dim shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div>
                  Запрос на доступ к{" "}
                  <span className="mono">prod-postgres-master</span>
                </div>
                <div className="text-xs text-dim">
                  от dave · ждёт твоего подтверждения
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Recent activity */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Активность в депе</h3>
            <Link to="/log" className="text-xs text-accent">
              Открыть полный лог →
            </Link>
          </div>
          <div className="text-sm">
            {SAMPLE_ACTIVITY.map((row) => (
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
          <b>Совет:</b> используй{" "}
          <span className="mono surface-2 px-1 rounded">⌘K</span> для
          быстрого поиска по всему депу — пользователи, серверы,
          credential&apos;ы, события.
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

const SAMPLE_ACTIVITY: ActivityRow[] = [
  { ts: "15:42", actor: "bob", action: "создал user", target: "igor", req: "req_7e9f...", badge: "success", badgeKind: "ok" },
  { ts: "14:18", actor: "alice", action: "revealed", target: "prod-postgres-master", req: "req_4ab1...", badge: "success", badgeKind: "ok" },
  { ts: "12:55", actor: "worker_bot", action: "завершил task", target: "tsk_fa12...", req: "req_aa10...", badge: "success", badgeKind: "ok" },
  { ts: "11:32", actor: "charlie", action: "failed login ×5", target: "", req: "req_22c8...", badge: "429", badgeKind: "warn" },
  { ts: "10:01", actor: "cron", action: "запустил pg-backup", target: "", req: "job_aut...", badge: "done", badgeKind: "ok" },
  { ts: "09:12", actor: "alice", action: "обновил role ACL", target: "", req: "req_001f...", badge: "success", badgeKind: "ok" },
];
