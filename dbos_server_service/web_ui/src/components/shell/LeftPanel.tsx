import type { MouseEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuthOptional } from "@/contexts/AuthContext";
import {
  Home,
  Users,
  Server,
  LockKeyhole,
  Cog,
  ListChecks,
  Package,
  ShieldCheck,
  FileText,
  Filter,
  Trash,
  LogOut,
  UserCog,
  PanelLeftClose,
  PanelLeftOpen,
  BookOpen,
  HardDrive,
  ExternalLink,
  MonitorPlay,
  type LucideIcon,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { useQuery } from "@/api/auth/useQuery";
import { getNavLinks } from "@/api/auth/navLinks";
import {
  hasServerZoneAccess,
  hasAuditLogAccess,
  hasSecretZoneAccess,
  isLogingRole,
} from "@/lib/rbac";
import type { ServiceName } from "@/types/persona";
import { useDeptLabelOpt } from "@/lib/labels";
import { formatFio } from "@/lib/fio";
import { ThemeSwitcher } from "./ThemeSwitcher";
import { AdminOnlyPanel } from "./AdminOnlyPanel";
import { NotificationBell } from "@/components/notifications/NotificationBell";

interface ServiceChip {
  service: ServiceName;
  to: string;
  icon: LucideIcon;
  label: string;
  hint?: string;
  subItems?: { to: string; icon: LucideIcon; label: string; hint?: string }[];
}

const SERVICE_CATALOG: Record<ServiceName, ServiceChip> = {
  auth: { service: "auth", to: "/users", icon: Users, label: "Пользователи" },
  server: {
    service: "server",
    to: "/server",
    icon: Server,
    label: "Серверы",
    subItems: [
      { to: "/server?only=servers", icon: Server, label: "Серверы" },
      { to: "/server?only=vms", icon: MonitorPlay, label: "ВМ" },
      { to: "/server/users", icon: Users, label: "Пользователи" },
      { to: "/server/packages", icon: Package, label: "Пакеты" },
      { to: "/server/tasks", icon: ListChecks, label: "Задачи" },
    ],
  },
  secret: { service: "secret", to: "/secret", icon: LockKeyhole, label: "Секреты" },
  // server_worker — часть server-зоны; задачи живут под «Серверами»
  // (/server/tasks), отдельного чипа нет. Запись остаётся ради полноты
  // ServiceName-каталога, в nav не рендерится.
  worker: {
    service: "worker",
    to: "/server/tasks",
    icon: Cog,
    label: "Workers",
  },
  logging: {
    service: "logging",
    to: "/log",
    icon: FileText,
    label: "Журнал аудита",
    subItems: [
      { to: "/log/rules", icon: Filter, label: "Правила" },
      { to: "/log/retention", icon: Trash, label: "Хранение" },
    ],
  },
  config: { service: "config", to: "/admin", icon: Cog, label: "Config" },
};

interface LeftPanelProps {
  width: number;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export function LeftPanel({ width, collapsed, onToggleCollapsed }: LeftPanelProps) {
  const { persona } = usePersona();
  const auth = useAuthOptional();
  const location = useLocation();
  // Хук обязан вызываться до любого условного return (rules-of-hooks): иначе
  // при смене роли в рамках сессии счётчик хуков разъезжается.
  const deptLabel = useDeptLabelOpt(persona.dept_id);

  // Настраиваемые кнопки (напр. «allta»): backend отдаёт только те, что видны
  // отделу пользователя. Хук зовём безусловно (rules-of-hooks), но для
  // account_admin выключаем — он видит развёрнутый AdminOnlyPanel без сервис-
  // навигации, кнопка ему не показывается. Ошибку/пустоту тихо гасим — кнопки
  // просто нет.
  const navLinksQ = useQuery(() => getNavLinks(), [], {
    enabled: persona.platform_role !== "account_admin",
    keepPreviousDataOnError: true,
  });
  const customNavLinks = navLinksQ.data ?? [];

  // account_admin живёт целиком в админ-каталоге — для него превращаем левую
  // панель в развёрнутый навигатор по /admin без «Главной» и сервис-чипов.
  if (persona.platform_role === "account_admin") {
    return (
      <AdminOnlyPanel
        width={width}
        collapsed={collapsed}
        onToggleCollapsed={onToggleCollapsed}
      />
    );
  }

  const onLogout = async (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (auth) await auth.logout();
  };

  // Users management lives under /admin/services.users — no separate chip.
  // Config тоже скрыт — кнопка «Администрирование» внизу уже ведёт на /admin.
  // account_admin сюда не доходит: для него выше рендерится AdminOnlyPanel.
  const serviceList = [...persona.accessible_services];
  // Аудит-чип завязан на read-доступ к журналу, а не на подключение отдела к
  // loging_service. У dep_admin / loging_reader_dep отдел к логированию не
  // подключён, поэтому `logging` не попадает в accessible_services — но раздел
  // им доступен (backend режет выдачу их отделом). Дотягиваем чип вручную,
  // фильтры ниже его уже не срежут.
  if (hasAuditLogAccess(persona) && !serviceList.includes("logging")) {
    serviceList.push("logging");
  }
  const chips: ServiceChip[] = serviceList
    // worker — часть server-зоны; задачи под «Серверами» (/server/tasks),
    // отдельного чипа нет. auth/config тоже без чипа.
    .filter((s) => s !== "auth" && s !== "config" && s !== "worker")
    // Audit log виден тем, у кого есть read-доступ к журналу: logging_admin /
    // logging_reader / logging_reader_dep / account_admin / dep_admin. У
    // dept-scoped ролей (включая dep_admin) backend режет выдачу своим отделом,
    // но раздел рабочий. Прочим персонам чип вёл бы в 403 — скрываем.
    .filter((s) => s !== "logging" || hasAuditLogAccess(persona))
    // Servers тоже гейтим по реальному доступу к server-зоне (вкл. подпункт
    // «Задачи»). У отдела подключён server_service, поэтому обычный
    // dept-пользователь без server.* роли получает `server` в
    // accessible_services, но backend отвечает 403 на список серверов и tasks —
    // чип вёл бы на пустую страницу с ошибкой.
    .filter((s) => s !== "server" || hasServerZoneAccess(persona))
    // Secret — dept-scoped: платформенные роли без отдела (account_admin /
    // logging_*) получают 403, чип вёл бы в BlockedPane. Прячем у них.
    .filter((s) => s !== "secret" || hasSecretZoneAccess(persona))
    .map((s) => SERVICE_CATALOG[s])
    .filter(Boolean)
    .map((chip) => {
      // Rules/Retention в loging_service закрыты backend'ом на loging_admin
      // (require_admin и на чтение). loging_reader доступен только просмотр
      // событий — не показываем ему подпункты, которые всё равно вернут 403.
      if (
        chip.service === "logging" &&
        persona.platform_role !== "logging_admin"
      ) {
        return { ...chip, subItems: undefined };
      }
      return chip;
    });

  // Подсвечиваем ровно один пункт — самый специфичный. Иначе при выборе
  // подкатегории (/log/rules) загорается и родитель (/log), и соседи с общим
  // префиксом. Собираем все рендеримые ссылки, выбираем ту, чей `to` — самый
  // длинный матч к текущему пути (точное равенство или префикс `to + "/"`).
  const navLinks = ["/home", "/wiki", "/os", "/me", "/admin"];
  for (const chip of chips) {
    navLinks.push(chip.to);
    for (const s of chip.subItems ?? []) navLinks.push(s.to);
  }
  // Пункты с query-фильтром («Серверы»/«ВМ» на одном пути /server) подсвечиваем,
  // когда все параметры ссылки присутствуют в текущем URL. Так «ВМ»
  // (/server?only=vms) остаётся активным и при открытой карточке ВМ
  // (/server?only=vms&vm=…), но не загорается на чужом only=servers.
  const matches = (to: string) => {
    const qIdx = to.indexOf("?");
    if (qIdx >= 0) {
      const path = to.slice(0, qIdx);
      if (location.pathname !== path) return false;
      const want = new URLSearchParams(to.slice(qIdx + 1));
      const have = new URLSearchParams(location.search);
      for (const [k, v] of want) {
        if (have.get(k) !== v) return false;
      }
      return true;
    }
    return (
      location.pathname === to ||
      (to !== "/home" && location.pathname.startsWith(to + "/"))
    );
  };
  let activeTo: string | null = null;
  for (const to of navLinks) {
    if (matches(to) && (activeTo === null || to.length > activeTo.length)) {
      activeTo = to;
    }
  }
  const isActive = (to: string) => to === activeTo;

  const roleLine = persona.platform_role
    ? persona.platform_role + (deptLabel ? ` · ${deptLabel}` : "")
    : Object.keys(persona.service_roles).join(", ") +
      (deptLabel ? ` · ${deptLabel}` : "");

  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

  // Показываем ФИО, если оно есть; login остаётся в подписи-tooltip.
  const displayName = formatFio(persona);
  const hasRealFio = displayName !== persona.username;

  return (
    <aside
      className="flex flex-col h-full overflow-hidden shrink-0 border-r border-token surface"
      style={{ width }}
    >
      <nav className="p-2 flex flex-col gap-0.5 shrink-0">
        <Link
          to="/home"
          title={collapsed ? "Главная" : undefined}
          className={`chip ${isActive("/home") ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
        >
          <Home className="w-5 h-5 text-accent shrink-0" />
          {!collapsed && <div className="flex-1 text-sm">Главная</div>}
        </Link>
        <Link
          to="/wiki"
          title={collapsed ? "Вики — примеры API" : undefined}
          className={`chip ${isActive("/wiki") ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
        >
          <BookOpen className="w-5 h-5 text-accent shrink-0" />
          {!collapsed && <div className="flex-1 text-sm">Вики</div>}
        </Link>
        <Link
          to="/os"
          title={collapsed ? "ОС — каталог версий" : undefined}
          className={`chip ${isActive("/os") ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
        >
          <HardDrive className="w-5 h-5 text-accent shrink-0" />
          {!collapsed && <div className="flex-1 text-sm">ОС</div>}
        </Link>
        {customNavLinks.map((link) => (
          <a
            key={`${link.label}-${link.url}`}
            href={link.url}
            target="_blank"
            rel="noreferrer"
            title={collapsed ? link.label : undefined}
            className={`chip ${collapsed ? "justify-center" : ""}`}
          >
            <ExternalLink className="w-5 h-5 text-accent shrink-0" />
            {!collapsed && <div className="flex-1 text-sm">{link.label}</div>}
          </a>
        ))}
      </nav>

      {!collapsed && (
        <div className="px-3 pt-4 pb-1 text-[11px] text-dim uppercase tracking-wider shrink-0">
          Сервисы
        </div>
      )}
      <nav className="flex-1 min-h-0 overflow-y-auto px-2 flex flex-col gap-0.5">
        {chips.map((chip) => (
          <div key={chip.service} className="flex flex-col">
            <Link
              to={chip.to}
              title={collapsed ? chip.label : undefined}
              className={`chip ${isActive(chip.to) ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
            >
              <chip.icon className="w-5 h-5 shrink-0" />
              {!collapsed && (
                <div className="flex-1">
                  <div className="text-sm">{chip.label}</div>
                  {chip.hint && (
                    <div className="text-[11px] text-dim">{chip.hint}</div>
                  )}
                </div>
              )}
            </Link>
            {!collapsed &&
              chip.subItems?.map((s) => (
                <Link
                  key={s.to}
                  to={s.to}
                  className={`subchip ${isActive(s.to) ? "active" : ""}`}
                >
                  <s.icon className="w-3.5 h-3.5" />
                  <span>{s.label}</span>
                </Link>
              ))}
          </div>
        ))}
      </nav>

      <div className="mt-auto shrink-0 flex flex-col">
        {persona.has_admin && !isLogingRole(persona) && (
          <div className="p-2 border-t border-token flex flex-col gap-0.5">
            <Link
              to="/admin"
              title={collapsed ? "Администрирование" : undefined}
              className={`chip ${isActive("/admin") ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
            >
              <ShieldCheck className="w-5 h-5 text-warn shrink-0" />
              {!collapsed && (
                <div className="flex-1">
                  <div className="text-sm">Администрирование</div>
                  <div className="text-[11px] text-dim">
                    {persona.dept_id ? "в рамках депа" : "глобально"}
                  </div>
                </div>
              )}
            </Link>
          </div>
        )}

        <div className="p-2 border-t border-token flex flex-col gap-2">
          <Link
            to="/me"
            title={
              collapsed
                ? `${displayName} (${persona.username}) — личный кабинет`
                : hasRealFio
                  ? persona.username
                  : "Личный кабинет"
            }
            className={`surface-2 border border-token rounded px-2 py-1.5 flex items-center gap-2 text-sm hover-bg transition-colors ${
              isActive("/me") ? "active" : ""
            } ${collapsed ? "justify-center" : ""}`}
          >
            <div className="w-7 h-7 rounded-full bg-accent flex items-center justify-center text-xs font-semibold shrink-0">
              {persona.initials}
            </div>
            {!collapsed && (
              <>
                <div className="min-w-0 flex-1">
                  <div className="leading-tight truncate">{displayName}</div>
                  <div className="text-[10px] text-dim leading-tight truncate" title={roleLine}>
                    {roleLine}
                  </div>
                </div>
                <UserCog className="w-4 h-4 text-dim shrink-0" aria-hidden="true" />
              </>
            )}
          </Link>

          {collapsed ? (
            <div className="flex justify-center">
              <NotificationBell />
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <NotificationBell />
              <div className="flex-1 min-w-0">
                <ThemeSwitcher />
              </div>
            </div>
          )}

          <div className={`flex items-center gap-2 ${collapsed ? "flex-col" : ""}`}>
            <button
              type="button"
              onClick={onToggleCollapsed}
              title={collapsed ? "Развернуть панель" : "Свернуть панель"}
              aria-label={collapsed ? "Развернуть панель" : "Свернуть панель"}
              className="btn flex items-center justify-center gap-1.5 shrink-0"
            >
              <ToggleIcon className="w-4 h-4" />
            </button>
            {!collapsed && (
              <button
                type="button"
                onClick={onLogout}
                className="btn flex-1 flex items-center justify-center gap-1.5"
              >
                <LogOut className="w-4 h-4" />
                Выйти
              </button>
            )}
            {collapsed && (
              <button
                type="button"
                onClick={onLogout}
                title="Выйти"
                aria-label="Выйти"
                className="btn flex items-center justify-center gap-1.5"
              >
                <LogOut className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
