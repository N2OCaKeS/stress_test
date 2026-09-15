import {
  useEffect,
  useMemo,
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
  Link2,
  ListChecks,
  Users,
  Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";
import { HomeShell } from "./HomeShell";
import { ServiceCredentialFields } from "./ServiceCredentialFields";
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
import { Toggle } from "@/components/ui/Toggle";
import { Modal } from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/contexts/ToastContext";
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
import {
  getDepartmentIntegrationSettings,
  upsertDepartmentIntegrationSettings,
} from "@/api/testing/departmentIntegrationSettings";
import {
  getDepartmentTestSettings,
  upsertDepartmentTestSettings,
} from "@/api/testing/departmentTestSettings";
import {
  createDepartmentReportMember,
  deleteDepartmentReportMember,
  listDepartmentReportMembers,
  updateDepartmentReportMember,
} from "@/api/testing/departmentReportMembers";
import type {
  DepartmentActivityReport,
  DepartmentIntegrationSettingsUpdateRequest,
  DepartmentReportMember,
} from "@/api/testing/types";
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
          <section className="mt-4 grid gap-4">
            <DepartmentIntegrationSettingsCard departmentId={myDeptId} />
            <DepartmentQueueSettingsCard departmentId={myDeptId} />
            <HrReportCard departmentId={myDeptId} />
            <DepartmentReportMembersCard departmentId={myDeptId} />
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

  // Лимит покрывает все 12 показанных месяцев с запасом на повторные попытки
  // (падение + повтор того же периода — новая строка, не upsert, см. модель).
  const historyQ = useQuery(
    () => listDepartmentActivityReports(departmentId, { limit: 36 }),
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
  // Список отсортирован backend'ом по generated_at DESC (см. репозиторий) —
  // первое вхождение периода при обходе сверху вниз и есть последняя попытка.
  const latestByPeriod = useMemo(() => {
    const map = new Map<string, DepartmentActivityReport>();
    for (const report of reports) {
      if (!map.has(report.period)) map.set(report.period, report);
    }
    return map;
  }, [reports]);

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <FileBarChart className="w-4 h-4 text-accent" />
          Отчёт по активностям сотрудников отдела
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
          {generating ? "Генерация…" : "Сгенерировать отчёт"}
        </Button>
      </div>
      {errorMsg && <div className="text-xs text-danger mb-3">{errorMsg}</div>}

      <div className="text-xs text-dim mb-2">
        Отчёты за последние 12 месяцев — какие уже есть, каких ещё нет
      </div>
      {historyQ.loading && reports.length === 0 ? (
        <div className="text-xs text-dim">Загрузка…</div>
      ) : (
        <div className="grid gap-2">
          {periodOptions.map(({ value }) => {
            const report = latestByPeriod.get(value);
            return report ? (
              <HrReportRow key={value} report={report} confluenceBaseUrl={confluenceBaseUrl} />
            ) : (
              <div
                key={value}
                className="surface-2 border border-token rounded p-2 flex items-center justify-between gap-3 text-sm opacity-70"
              >
                <span className="font-medium">{periodLabel(value)}</span>
                <span className="text-xs text-dim">не создан</span>
              </div>
            );
          })}
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

// ── Настройки интеграции отдела (testing_service) ──────────────────────────

/**
 * Поля `department_integration_settings` в порядке отображения. Ключи должны
 * дословно совпадать с `DepartmentIntegrationSettingsUpdateRequest` —
 * читаем/пишем их через generic `Record<string, string | null>`, не
 * перечисляя каждое поле руками.
 */
const INTEGRATION_FIELDS: Array<{ key: string; label: string; placeholder?: string; mono?: boolean }> = [
  { key: "jira_base_url", label: "Jira base URL", placeholder: "https://jira.astralinux.ru", mono: true },
  { key: "confluence_base_url", label: "Confluence base URL", placeholder: "https://confluence.astralinux.ru", mono: true },
  { key: "bitbucket_base_url", label: "Bitbucket base URL", placeholder: "https://bitbucket.astralinux.ru", mono: true },
  { key: "bitbucket_project_key", label: "Bitbucket project key", placeholder: "PROJ" },
  { key: "bitbucket_repo_slug", label: "Bitbucket repo slug", placeholder: "my-repo" },
  { key: "jira_board_id", label: "Jira board id", placeholder: "42" },
  { key: "tempo_team_id", label: "Tempo team id", placeholder: "7" },
  { key: "confluence_report_page_space", label: "Confluence space для отчёта по активностям", placeholder: "DEPT" },
  { key: "confluence_report_parent_page_title", label: "Родительская страница отчёта по активностям", placeholder: "Отчёты по активности" },
  { key: "stp_matrix_confluence_space", label: "Confluence space для СТП-матрицы", placeholder: "DEPTQA" },
  { key: "stp_matrix_confluence_root_page_title", label: "Корневая страница СТП-матрицы", placeholder: "Состав тестового прогона" },
  { key: "credential_id", label: "Credential id (Jira/Zephyr)", placeholder: "cred_...", mono: true },
  { key: "bitbucket_credential_id", label: "Credential id (Bitbucket)", placeholder: "cred_...", mono: true },
];

/**
 * Настройки отдела для внешних интеграций (Jira/Zephyr/Confluence/Bitbucket) —
 * без них ни СТП+Zephyr, ни HR-отчёт по активности физически не работают.
 * `credential_id`/`bitbucket_credential_id` — только id учётных данных в
 * `secret_service`, сам секрет заводится там же (страница «Секреты»).
 */
function DepartmentIntegrationSettingsCard({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const settingsQ = useQuery(() => getDepartmentIntegrationSettings(departmentId), [departmentId]);
  const loaded = settingsQ.data as unknown as Record<string, string | null> | undefined;
  const [form, setForm] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    const next: Record<string, string> = {};
    for (const f of INTEGRATION_FIELDS) next[f.key] = loaded[f.key] ?? "";
    setForm(next);
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return INTEGRATION_FIELDS.some((f) => (form[f.key] ?? "").trim() !== (loaded[f.key] ?? ""));
  }, [loaded, form]);

  async function handleSave() {
    if (pending || !loaded) return;
    setPending(true);
    try {
      const body: Record<string, string | null> = {};
      for (const f of INTEGRATION_FIELDS) {
        const v = (form[f.key] ?? "").trim();
        body[f.key] = v === "" ? null : v;
      }
      await upsertDepartmentIntegrationSettings(
        departmentId,
        body as DepartmentIntegrationSettingsUpdateRequest,
      );
      toast.success("Настройки интеграции отдела сохранены");
      settingsQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить настройки интеграции"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <Link2 className="w-4 h-4 text-accent" />
          Интеграции отдела (Jira / Confluence / Bitbucket)
        </h3>
        <span className="text-xs text-dim">testing_service</span>
      </div>
      <div className="text-xs text-dim mb-3">
        Нужны для генерации СТП (Zephyr), публикации СТП-матрицы и отчёта по активностям отдела в Confluence. Сами токены/пароли
        заводятся в <Link to="/secret/service" className="text-accent">сервисных учётных данных</Link>.
        Выберите нужные записи вашего отдела ниже.
      </div>

      {settingsQ.loading && !loaded && <div className="text-xs text-dim py-2">Загрузка…</div>}
      {!settingsQ.loading && settingsQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(settingsQ.error, "Настройки не загрузились")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => settingsQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}

      {loaded && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {INTEGRATION_FIELDS.filter((f) => !["credential_id", "bitbucket_credential_id"].includes(f.key)).map((f) => (
              <label key={f.key} className="flex flex-col gap-1 text-sm">
                <span className="field-label">{f.label}</span>
                <input
                  className={`field-input ${f.mono ? "mono" : ""}`.trim()}
                  value={form[f.key] ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                />
              </label>
            ))}
            <ServiceCredentialFields departmentId={departmentId} values={form} disabled={pending}
              onChange={(key, value) => setForm((prev) => ({ ...prev, [key]: value }))} />
          </div>
          <div className="flex items-center gap-3 mt-3">
            <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
          </div>
        </>
      )}
    </div>
  );
}

// ── Настройки очереди/ретраев тестирования отдела (testing_service) ────────

/**
 * `department_test_settings` — ретрай провалившихся прогонов, имя учётки
 * исполнения теста на стенде, расписание HR-отчёта (заглушка волны 10 —
 * планировщика ещё нет, поле только хранится).
 */
function DepartmentQueueSettingsCard({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const settingsQ = useQuery(() => getDepartmentTestSettings(departmentId), [departmentId]);
  const loaded = settingsQ.data;
  const [retryEnabled, setRetryEnabled] = useState(true);
  const [testUsername, setTestUsername] = useState("");
  const [schedule, setSchedule] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    setRetryEnabled(loaded.retry_enabled);
    setTestUsername(loaded.test_username);
    setSchedule(loaded.activity_report_schedule ?? "");
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return (
      retryEnabled !== loaded.retry_enabled ||
      testUsername.trim() !== loaded.test_username ||
      schedule.trim() !== (loaded.activity_report_schedule ?? "")
    );
  }, [loaded, retryEnabled, testUsername, schedule]);

  async function handleSave() {
    if (pending || !loaded) return;
    const username = testUsername.trim();
    if (!username) {
      toast.error("Укажите имя пользователя исполнения теста.");
      return;
    }
    setPending(true);
    try {
      await upsertDepartmentTestSettings(departmentId, {
        retry_enabled: retryEnabled,
        test_username: username,
        activity_report_schedule: schedule.trim() || null,
      });
      toast.success("Настройки очереди тестирования сохранены");
      settingsQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить настройки очереди"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <ListChecks className="w-4 h-4 text-accent" />
          Очередь и повторы тестирования
        </h3>
        <span className="text-xs text-dim">testing_service</span>
      </div>

      {settingsQ.loading && !loaded && <div className="text-xs text-dim py-2">Загрузка…</div>}
      {!settingsQ.loading && settingsQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(settingsQ.error, "Настройки не загрузились")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => settingsQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}

      {loaded && (
        <>
          <div className="flex flex-col gap-3 max-w-md">
            <Toggle
              label="Повторять провалившиеся тесты в прогоне"
              checked={retryEnabled}
              onChange={(e) => setRetryEnabled(e.target.checked)}
            />
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">Пользователь исполнения теста на стенде</span>
              <input
                className="field-input mono"
                value={testUsername}
                onChange={(e) => setTestUsername(e.target.value)}
                placeholder="u"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">
                Расписание отчёта по активностям
                <span className="text-dim text-xs ml-1">(планировщик ещё не реализован, значение только хранится)</span>
              </span>
              <input
                className="field-input"
                value={schedule}
                onChange={(e) => setSchedule(e.target.value)}
                placeholder="например, 1 числа месяца"
              />
            </label>
          </div>
          <div className="flex items-center gap-3 mt-3">
            <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
          </div>
        </>
      )}
    </div>
  );
}

// ── Сотрудники отдела для HR-отчёта (testing_service) ───────────────────────

/**
 * CRUD-список `department_report_members` — только сотрудники из этого
 * списка учитываются при генерации HR-отчёта (`HrReportCard` выше). Логично
 * лежит рядом с самим отчётом: список сотрудников — вход, отчёт — выход.
 */
function DepartmentReportMembersCard({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const membersQ = useQuery(
    () => listDepartmentReportMembers(departmentId, { limit: 200 }),
    [departmentId],
  );
  const [modalMember, setModalMember] = useState<DepartmentReportMember | "new" | null>(null);

  const members = membersQ.data?.items ?? [];

  async function handleDelete(member: DepartmentReportMember) {
    if (
      !(await confirm({
        title: "Удалить сотрудника",
        message: `Удалить ${member.display_name} из списка сотрудников для отчёта по активностям?`,
        confirmLabel: "Удалить",
        danger: true,
      }))
    )
      return;
    try {
      await deleteDepartmentReportMember(departmentId, member.id);
      toast.success(`${member.display_name} удалён из отчёта по активностям`);
      membersQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось удалить сотрудника"));
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="font-semibold flex items-center gap-2">
          <Users className="w-4 h-4 text-accent" />
          Сотрудники отдела для отчёта по активностям
        </h3>
        <Button
          variant="primary"
          size="sm"
          type="button"
          className="flex items-center gap-1"
          onClick={() => setModalMember("new")}
        >
          <UserPlus className="w-3.5 h-3.5" /> Добавить
        </Button>
      </div>
      <div className="text-xs text-dim mb-3">
        Коммиты, комментарии в Jira по спринтам и часы Tempo учитываются только
        для сотрудников из этого списка.
      </div>

      {membersQ.loading && members.length === 0 && (
        <div className="text-xs text-dim py-2">Загрузка…</div>
      )}
      {!membersQ.loading && membersQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(membersQ.error, "Список не загрузился")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => membersQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}
      {!membersQ.loading && membersQ.error == null && members.length === 0 && (
        <div className="text-sm text-dim text-center py-4">Список пуст.</div>
      )}
      {members.length > 0 && (
        <div className="flex flex-col gap-1">
          {members.map((m) => (
            <ReportMemberRow
              key={m.id}
              member={m}
              onEdit={() => setModalMember(m)}
              onDelete={() => handleDelete(m)}
            />
          ))}
        </div>
      )}

      {modalMember != null && (
        <ReportMemberModal
          departmentId={departmentId}
          member={modalMember === "new" ? null : modalMember}
          onClose={() => setModalMember(null)}
          onSaved={() => {
            setModalMember(null);
            membersQ.refetch();
          }}
        />
      )}
    </div>
  );
}

function ReportMemberRow({
  member,
  onEdit,
  onDelete,
}: {
  member: DepartmentReportMember;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="surface-2 border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap">
      <div className="flex-1 min-w-[160px]">
        <div className="text-sm flex items-center gap-2">
          {member.display_name}
          {!member.is_active && <span className="text-xs text-dim">(не учитывается)</span>}
        </div>
        <div className="text-[11px] text-dim mono truncate">
          {member.bitbucket_username ?? "—"} · {member.jira_author_name ?? "—"} ·{" "}
          {member.jira_tempo_worker_key ?? "—"}
        </div>
      </div>
      <Button variant="ghost" size="sm" type="button" onClick={onEdit}>
        Изменить
      </Button>
      <Button variant="danger" size="sm" type="button" className="flex items-center gap-1" onClick={onDelete}>
        <Trash2 className="w-3.5 h-3.5" /> Удалить
      </Button>
    </div>
  );
}

function ReportMemberModal({
  departmentId,
  member,
  onClose,
  onSaved,
}: {
  departmentId: string;
  member: DepartmentReportMember | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [displayName, setDisplayName] = useState(member?.display_name ?? "");
  const [bitbucketUsername, setBitbucketUsername] = useState(member?.bitbucket_username ?? "");
  const [jiraAuthorName, setJiraAuthorName] = useState(member?.jira_author_name ?? "");
  const [tempoWorkerKey, setTempoWorkerKey] = useState(member?.jira_tempo_worker_key ?? "");
  const [isActive, setIsActive] = useState(member?.is_active ?? true);
  const [pending, setPending] = useState(false);

  async function handleSubmit() {
    const name = displayName.trim();
    if (!name) {
      toast.error("Укажите имя сотрудника.");
      return;
    }
    setPending(true);
    try {
      const body = {
        display_name: name,
        bitbucket_username: bitbucketUsername.trim() || null,
        jira_author_name: jiraAuthorName.trim() || null,
        jira_tempo_worker_key: tempoWorkerKey.trim() || null,
        is_active: isActive,
      };
      if (member) {
        await updateDepartmentReportMember(departmentId, member.id, body);
        toast.success(`${name} обновлён`);
      } else {
        await createDepartmentReportMember(departmentId, body);
        toast.success(`${name} добавлен`);
      }
      onSaved();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить сотрудника"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={member ? "Изменить сотрудника" : "Добавить сотрудника"}
      icon={<Users className="w-5 h-5 text-accent" />}
      footer={
        <>
          <Button variant="default" type="button" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary" type="button" onClick={handleSubmit} disabled={pending}>
            {pending ? "Сохраняем…" : "Сохранить"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">ФИО / отображаемое имя</span>
          <input
            className="field-input"
            autoFocus
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Иванов Иван"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Bitbucket username</span>
          <input
            className="field-input mono"
            value={bitbucketUsername}
            onChange={(e) => setBitbucketUsername(e.target.value)}
            placeholder="ivanov"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Jira author name</span>
          <input
            className="field-input mono"
            value={jiraAuthorName}
            onChange={(e) => setJiraAuthorName(e.target.value)}
            placeholder="Ivan Ivanov"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Tempo worker key</span>
          <input
            className="field-input mono"
            value={tempoWorkerKey}
            onChange={(e) => setTempoWorkerKey(e.target.value)}
            placeholder="JIRAUSER10123"
          />
        </label>
        <Toggle
          label="Учитывать в следующем отчёте"
          checked={isActive}
          onChange={(e) => setIsActive(e.target.checked)}
        />
      </div>
    </Modal>
  );
}
