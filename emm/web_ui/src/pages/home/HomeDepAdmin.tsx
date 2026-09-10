import {
  useEffect,
  useRef,
  useState,
} from "react";
import {
  UserPlus,
  ServerCog,
  KeyRound,
  Activity,
  AlertTriangle,
  AlertCircle,
  FileBarChart,
  HardDrive,
  ShieldAlert,
  Clock,
  Lightbulb,
} from "lucide-react";
import { Link } from "react-router-dom";
import { HomeShell } from "./HomeShell";
import { usePersona } from "@/contexts/PersonaContext";
import {
  hasAuditLogAccess,
  isDepAdmin,
  isPlatformWideAdmin,
  personaDeptId,
} from "@/lib/rbac";
import { useDeptLabel } from "@/lib/labels";
import { USERS } from "@/mocks/auth";
import { SERVERS } from "@/mocks/server";
import { CREDENTIALS } from "@/mocks/secret";
import { TASKS } from "@/mocks/worker";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { AUDIT_EVENTS } from "@/mocks/log";
import { listUsers, listUsersByDepartment } from "@/api/auth/users";
import { listGroups } from "@/api/auth/groups";
import { listBots } from "@/api/auth/bots";
import { getHostDiskUsage } from "@/api/server/misc";
import type { HostDiskPathUsage, HostDiskUsageResponse } from "@/api/server/types";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { formatMsk } from "@/lib/datetime";
import {
  generateDepartmentActivityReport,
  listDepartmentActivityReports,
} from "@/api/testing/departmentActivityReports";
import { getDepartmentIntegrationSettings } from "@/api/testing/departmentIntegrationSettings";
import type { DepartmentActivityReport } from "@/api/testing/types";
import { RecentAuditEvents } from "./widgets/RecentAuditEvents";
import { AllTasksWidget } from "./widgets/AllTasksWidget";

/**
 * Department-admin Home.
 * Welcome → Quick tiles → Stats → 2-column bottom row → Tip footer.
 */
export function HomeDepAdmin() {
  const { persona } = usePersona();
  const mockMode = useMockMode();
  const myDeptId = personaDeptId(persona);
  // Этот Home обслуживает и dep_admin, и обычного user'а (default-ветка
  // диспетчера). Управление группами видно только тем, кто реально управляет
  // отделом; блок лога — только при доступе к чтению аудита. Обычный user без
  // platform-роли не должен видеть ни «Группы», ни «Логи».
  const canManageGroups = isDepAdmin(persona) || isPlatformWideAdmin(persona);
  const canAudit = hasAuditLogAccess(persona);
  // Имя отдела резолвим через общий кэш меток: dep_admin не видит глобальный
  // список отделов, но собственный отдел LabelsProvider сидит из identity.
  const deptName = useDeptLabel(persona.dept_id ?? null);

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
  const hostDiskQ = useQuery(
    () => getHostDiskUsage(),
    [],
    { enabled: !mockMode },
  );
  const hostDiskRefetchRef = useRef(hostDiskQ.refetch);
  hostDiskRefetchRef.current = hostDiskQ.refetch;

  useEffect(() => {
    if (mockMode) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") hostDiskRefetchRef.current();
    }, 5_000);
    return () => window.clearInterval(id);
  }, [mockMode]);

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
            <>Отдел <b>{deptName}</b></>
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
            <div className="stat-label">Пользователи (область)</div>
            <div className="stat-big">{anyLoading ? "—" : liveUsers}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
          <div className="card">
            <div className="stat-label">Группы</div>
            <div className="stat-big">{anyLoading ? "—" : liveGroups}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
          <div className="card">
            <div className="stat-label">Боты</div>
            <div className="stat-big">{anyLoading ? "—" : liveBots}</div>
            <div className="text-xs text-dim mt-2">auth_service</div>
          </div>
        </section>

        {(canManageGroups || canAudit) && (
          <section className="grid gap-4 md:grid-cols-2">
            {canManageGroups && (
              <EmmDiskUsageCard data={hostDiskQ.data ?? null} loading={hostDiskQ.loading} />
            )}
            {canAudit && <RecentAuditEvents />}
          </section>
        )}

        {canManageGroups && myDeptId && (
          <section className="mt-4">
            <HrReportCard departmentId={myDeptId} />
          </section>
        )}

        {canManageGroups && (
          <section className="mt-4">
            <AllTasksWidget />
          </section>
        )}
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
            Отдел <b>{deptName}</b> · {usersInScope.length}{" "}
            пользователей · {serversInScope.length} серверов ·{" "}
            {credsInScope.length} доступных учётных данных
          </>
        ) : (
          <>
            Платформа · {USERS.length} пользователей · {SERVERS.length}{" "}
            серверов · {CREDENTIALS.length} учётных данных
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
              <span className="text-sm font-medium">Создать учётные данные</span>
            </div>
            <div className="text-xs text-dim">Токен / пароль / ключ</div>
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
          <div className="stat-label">Серверы онлайн</div>
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
          <div className="stat-label">Задачи за 24ч</div>
          <div className="stat-big">{TASKS.length * 3 - 3}</div>
          <div className="text-xs text-dim mt-2">
            {failed} провалено · {retry} повтор
          </div>
        </div>
        <div className="card">
          <div className="stat-label">События аудита</div>
          <div className="stat-big">
            {AUDIT_EVENTS.length.toLocaleString("ru-RU")}
          </div>
          <div className="text-xs text-dim mt-2">
            {critical} критических · {warning} предупреждений
          </div>
        </div>
        <div className="card">
          <div className="stat-label">Учётные данные</div>
          <div className="stat-big">{credsInScope.length}</div>
          <div className="text-xs text-dim mt-2">
            {expiring} истекает на этой неделе
          </div>
        </div>
      </section>

      {/* Bottom two columns */}
      <section className="grid gap-4 md:grid-cols-2">
        <EmmDiskUsageCard data={MOCK_HOST_DISK_USAGE} loading={false} />

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
                  Истекают учётные данные{" "}
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
                  Сервер <span className="mono">srv-node-17</span> — IPMI
                  недоступен
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
                  Пользователь <span className="mono">charlie</span> заблокирован
                </div>
                <div className="text-xs text-dim">
                  5 неудачных входов за 10 мин · ручной разбор
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

        {/* Recent activity — только при доступе к чтению аудита. */}
        {canAudit && (
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
                  <Badge kind={row.badgeKind}>
                    {row.badge}
                  </Badge>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* Tip footer */}
      <section className="mt-8 card flex items-center gap-3 text-sm">
        <Lightbulb className="w-5 h-5 text-warn shrink-0" />
        <div>
          <b>Совет:</b> используй{" "}
          <span className="mono surface-2 px-1 rounded">⌘K</span> для
          быстрого поиска по всему депу — пользователи, серверы,
          учётные данные, события.
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
  { ts: "15:42", actor: "bob", action: "создал пользователя", target: "igor", req: "req_7e9f...", badge: "success", badgeKind: "ok" },
  { ts: "14:18", actor: "alice", action: "раскрыл", target: "prod-postgres-master", req: "req_4ab1...", badge: "success", badgeKind: "ok" },
  { ts: "12:55", actor: "worker_bot", action: "завершил задачу", target: "tsk_fa12...", req: "req_aa10...", badge: "success", badgeKind: "ok" },
  { ts: "11:32", actor: "charlie", action: "неудачный вход ×5", target: "", req: "req_22c8...", badge: "429", badgeKind: "warn" },
  { ts: "10:01", actor: "cron", action: "запустил pg-backup", target: "", req: "job_aut...", badge: "done", badgeKind: "ok" },
  { ts: "09:12", actor: "alice", action: "обновил ACL роли", target: "", req: "req_001f...", badge: "success", badgeKind: "ok" },
];

const MOCK_HOST_DISK_USAGE: HostDiskUsageResponse = {
  paths: [
    { path: "/", total_gb: 62, used_gb: 56, used_percent: 90.3, available: true, error: null },
    { path: "/srv/ftp", total_gb: 126, used_gb: 112, used_percent: 88.9, available: true, error: null },
    { path: "/home/partimag", total_gb: 3022, used_gb: 878, used_percent: 29.1, available: true, error: null },
  ],
};

function EmmDiskUsageCard({
  data,
  loading,
}: {
  data: HostDiskUsageResponse | null;
  loading: boolean;
}) {
  const paths = data?.paths ?? [];
  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <HardDrive className="w-4 h-4 text-accent" />
          Диски EMM
        </h3>
        <span className="text-xs text-dim">хост платформы</span>
      </div>
      <div className="text-xs text-dim mb-3">
        {loading && paths.length === 0
          ? "Загрузка…"
          : "Заполняемость диска на хосте самого server_service."}
      </div>
      <div className="grid gap-2">
        {paths.map((path) => (
          <DiskUsageRow key={path.path} path={path} />
        ))}
      </div>
    </div>
  );
}

const ERROR_LABELS: Record<string, string> = {
  not_mounted: "не смонтирован",
  permission_denied: "нет доступа",
  unavailable: "недоступен",
};

function DiskUsageRow({ path }: { path: HostDiskPathUsage }) {
  const { used_percent: percent, used_gb: used, total_gb: size } = path;
  const tone =
    percent == null ? "bg-[var(--border)]" : percent >= 90 ? "bg-[var(--danger)]" : percent >= 75 ? "bg-[var(--warn)]" : "bg-[var(--ok)]";
  return (
    <div className="surface-2 border border-token rounded p-3">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="mono">{path.path}</span>
        <span className="mono text-xs text-dim">
          {path.available && used != null && size != null
            ? `${used.toFixed(1)} / ${size.toFixed(1)} GB`
            : "нет данных"}
        </span>
      </div>
      {path.available ? (
        <>
          <div className="mt-2 h-2 rounded-full surface border border-token overflow-hidden">
            <span
              className={`block h-full ${tone}`}
              style={{ width: `${Math.max(0, Math.min(100, percent ?? 0))}%` }}
            />
          </div>
          <div className="mt-1 text-xs text-dim flex justify-end">
            <span>{percent != null ? `${percent}%` : "—"}</span>
          </div>
        </>
      ) : (
        <div className="mt-1 text-xs text-dim">
          {ERROR_LABELS[path.error ?? ""] ?? "нет данных"}
        </div>
      )}
    </div>
  );
}

// ── HR-отчёт по активности (testing_service, ALLTA MIGRATION.md §9.1) ──────

const REPORT_STATUS_LABELS: Record<string, { label: string; className: string }> = {
  generating: { label: "генерируется", className: "text-warn" },
  done: { label: "готов", className: "text-ok" },
  failed: { label: "ошибка", className: "text-danger" },
};

/** `'YYYY-MM'` текущего месяца по МСК — дефолт формы и первая опция дропдауна. */
function currentPeriod(): string {
  const now = new Date();
  const msk = new Date(now.toLocaleString("en-US", { timeZone: "Europe/Moscow" }));
  return `${msk.getFullYear()}-${String(msk.getMonth() + 1).padStart(2, "0")}`;
}

const PERIOD_MONTH_LABELS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

function periodLabel(period: string): string {
  const [year, month] = period.split("-");
  const idx = Number(month) - 1;
  if (!year || Number.isNaN(idx) || idx < 0 || idx > 11) return period;
  return `${PERIOD_MONTH_LABELS[idx]} ${year}`;
}

/** Последние `count` месяцев (включая текущий), самый свежий первым. */
function recentPeriods(count: number): { value: string; label: string }[] {
  const now = new Date();
  const msk = new Date(now.toLocaleString("en-US", { timeZone: "Europe/Moscow" }));
  const options: { value: string; label: string }[] = [];
  for (let i = 0; i < count; i += 1) {
    const d = new Date(msk.getFullYear(), msk.getMonth() - i, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    options.push({ value, label: periodLabel(value) });
  }
  return options;
}

/**
 * Замена legacy-паттерна «поправить MONTH в коде и перезапустить скрипт»
 * (`libreport.py`/`monthly_report.py`) — ручная генерация HR-отчёта отдела
 * прямо с Home. Расписание периодической генерации — отдельная настройка в
 * администрировании отдела (`department_test_settings.activity_report_schedule`),
 * здесь только разовый запуск + история.
 */
function HrReportCard({ departmentId }: { departmentId: string }) {
  const periodOptions = recentPeriods(12);
  const [period, setPeriod] = useState(() => currentPeriod());
  const [generating, setGenerating] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const historyQ = useQuery(
    () => listDepartmentActivityReports(departmentId, { limit: 10 }),
    [departmentId],
  );
  const integrationQ = useQuery(
    () => getDepartmentIntegrationSettings(departmentId),
    [departmentId],
  );
  const confluenceBaseUrl = integrationQ.data?.confluence_base_url ?? null;

  const handleGenerate = async () => {
    setGenerating(true);
    setErrorMsg(null);
    try {
      await generateDepartmentActivityReport(departmentId, { period });
      historyQ.refetch();
    } catch (err) {
      setErrorMsg(apiErrMsg(err));
    } finally {
      setGenerating(false);
    }
  };

  const reports = historyQ.data?.items ?? [];

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <FileBarChart className="w-4 h-4 text-accent" />
          HR-отчёт по активности
        </h3>
        <span className="text-xs text-dim">testing_service</span>
      </div>
      <div className="text-xs text-dim mb-3">
        Коммиты, комментарии в Jira по спринтам и часы Tempo по отделу за выбранный месяц — публикуется на Confluence.
      </div>
      <div className="flex items-end gap-2 flex-wrap mb-3">
        <label className="grid gap-1">
          <span className="text-xs text-dim">Период</span>
          <Dropdown
            mode="single"
            options={periodOptions}
            value={period}
            onChange={setPeriod}
          />
        </label>
        <Button
          variant="primary"
          size="sm"
          type="button"
          onClick={handleGenerate}
          disabled={generating}
        >
          {generating ? "Генерация…" : "Сгенерировать HR-отчёт"}
        </Button>
      </div>
      {errorMsg && <div className="text-xs text-danger mb-3">{errorMsg}</div>}

      <div className="text-xs text-dim mb-2">История генераций</div>
      {historyQ.loading && reports.length === 0 ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : reports.length === 0 ? (
        <div className="text-xs text-dim">Отчётов ещё не было.</div>
      ) : (
        <div className="grid gap-2">
          {reports.map((report) => (
            <HrReportRow key={report.id} report={report} confluenceBaseUrl={confluenceBaseUrl} />
          ))}
        </div>
      )}
    </div>
  );
}

function HrReportRow({
  report,
  confluenceBaseUrl,
}: {
  report: DepartmentActivityReport;
  confluenceBaseUrl: string | null;
}) {
  const status = REPORT_STATUS_LABELS[report.status] ?? { label: report.status, className: "text-dim" };
  // Ссылка собирается только при известном `confluence_base_url` отдела
  // (`department_integration_settings`) — без него показываем голый id
  // страницы, а не гадаем публичный домен Confluence.
  const confluenceUrl = report.confluence_page_id && confluenceBaseUrl
    ? `${confluenceBaseUrl.replace(/\/$/, "")}/pages/viewpage.action?pageId=${report.confluence_page_id}`
    : null;
  return (
    <div className="surface-2 border border-token rounded p-2 flex items-center justify-between gap-3 text-sm">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium">{periodLabel(report.period)}</span>
          <span className={`text-xs ${status.className}`}>{status.label}</span>
        </div>
        <div className="text-xs text-dim mt-0.5">
          {formatMsk(report.generated_at)}
          {report.error ? ` · ${report.error}` : ""}
        </div>
      </div>
      {confluenceUrl ? (
        <a href={confluenceUrl} target="_blank" rel="noreferrer" className="text-xs text-accent shrink-0">
          Открыть в Confluence →
        </a>
      ) : report.confluence_page_id ? (
        <span className="text-xs text-dim mono shrink-0">page {report.confluence_page_id}</span>
      ) : null}
    </div>
  );
}
