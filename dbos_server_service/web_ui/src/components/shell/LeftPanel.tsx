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
  Skull,
  ShieldCheck,
  FileText,
  Filter,
  Trash,
  LogOut,
  UserCog,
  PanelLeftClose,
  PanelLeftOpen,
  type LucideIcon,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import type { ServiceName } from "@/types/persona";
import { useDeptLabelOpt } from "@/lib/labels";
import { ThemeSwitcher } from "./ThemeSwitcher";
import { AdminOnlyPanel } from "./AdminOnlyPanel";

interface ServiceChip {
  service: ServiceName;
  to: string;
  icon: LucideIcon;
  label: string;
  hint?: string;
  subItems?: { to: string; icon: LucideIcon; label: string; hint?: string }[];
}

const SERVICE_CATALOG: Record<ServiceName, ServiceChip> = {
  auth: { service: "auth", to: "/users", icon: Users, label: "Users" },
  server: { service: "server", to: "/server", icon: Server, label: "Servers" },
  secret: { service: "secret", to: "/secret", icon: LockKeyhole, label: "Secrets" },
  worker: {
    service: "worker",
    to: "/worker",
    icon: Cog,
    label: "Workers",
    hint: "tasks · dept",
    subItems: [
      { to: "/worker", icon: ListChecks, label: "Tasks" },
      { to: "/worker/dlq", icon: Skull, label: "DLQ" },
    ],
  },
  logging: {
    service: "logging",
    to: "/log",
    icon: FileText,
    label: "Audit log",
    subItems: [
      { to: "/log/rules", icon: Filter, label: "Rules" },
      { to: "/log/retention", icon: Trash, label: "Retention" },
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
  const chips: ServiceChip[] = persona.accessible_services
    .filter((s) => s !== "auth" && s !== "config")
    .map((s) => SERVICE_CATALOG[s])
    .filter(Boolean);

  const isActive = (to: string) =>
    location.pathname === to ||
    (to !== "/home" && location.pathname.startsWith(to + "/"));

  const deptLabel = useDeptLabelOpt(persona.dept_id);
  const roleLine = persona.platform_role
    ? persona.platform_role + (deptLabel ? ` · ${deptLabel}` : "")
    : Object.keys(persona.service_roles).join(", ") +
      (deptLabel ? ` · ${deptLabel}` : "");

  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

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
        {persona.has_admin && (
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
            title={collapsed ? `${persona.username} — личный кабинет` : "Личный кабинет"}
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
                  <div className="leading-tight truncate">{persona.username}</div>
                  <div className="text-[10px] text-dim leading-tight truncate" title={roleLine}>
                    {roleLine}
                  </div>
                </div>
                <UserCog className="w-4 h-4 text-dim shrink-0" aria-hidden="true" />
              </>
            )}
          </Link>

          {!collapsed && <ThemeSwitcher />}

          <div className={`flex items-center gap-2 ${collapsed ? "flex-col" : ""}`}>
            <button
              type="button"
              onClick={onToggleCollapsed}
              title={collapsed ? "Развернуть панель" : "Свернуть панель"}
              aria-label={collapsed ? "Expand left panel" : "Collapse left panel"}
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
                Logout
              </button>
            )}
            {collapsed && (
              <button
                type="button"
                onClick={onLogout}
                title="Logout"
                aria-label="Logout"
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
