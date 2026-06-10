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
  type LucideIcon,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import type { ServiceName } from "@/types/persona";
import { DEPT_DISPLAY_NAMES } from "@/lib/rbac";
import { ThemeSwitcher } from "./ThemeSwitcher";

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

export function LeftPanel() {
  const { persona } = usePersona();
  const auth = useAuthOptional();
  const location = useLocation();

  const onLogout = async (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (auth) await auth.logout();
  };

  // Users management lives under /admin/services.users — no separate chip.
  // Audit log is for logging admins/readers only; account_admin focuses on
  // platform-level admin and accesses audit through the dedicated logging-*
  // personas if needed. Config is hidden too — the dedicated "Администрирование"
  // entry at the bottom already opens /admin.
  const isAccountAdmin = persona.platform_role === "account_admin";
  const chips: ServiceChip[] = persona.accessible_services
    .filter((s) => s !== "auth" && s !== "config")
    .filter((s) => !(s === "logging" && isAccountAdmin))
    .map((s) => SERVICE_CATALOG[s])
    .filter(Boolean);

  const isActive = (to: string) =>
    location.pathname === to ||
    (to !== "/home" && location.pathname.startsWith(to + "/"));

  const deptLabel = persona.dept_id ? DEPT_DISPLAY_NAMES[persona.dept_id] ?? persona.dept_id : null;
  const roleLine = persona.platform_role
    ? persona.platform_role + (deptLabel ? ` · ${deptLabel}` : "")
    : Object.keys(persona.service_roles).join(", ") +
      (deptLabel ? ` · ${deptLabel}` : "");

  return (
    <aside className="flex flex-col h-full overflow-hidden w-[220px] shrink-0 border-r border-token surface">
      <nav className="p-2 flex flex-col gap-0.5 shrink-0">
        <Link to="/home" className={`chip ${isActive("/home") ? "active" : ""}`}>
          <Home className="w-5 h-5 text-accent" />
          <div className="flex-1 text-sm">Главная</div>
        </Link>
      </nav>

      <div className="px-3 pt-4 pb-1 text-[11px] text-dim uppercase tracking-wider shrink-0">
        Сервисы
      </div>
      <nav className="flex-1 min-h-0 overflow-y-auto px-2 flex flex-col gap-0.5">
        {chips.map((chip) => (
          <div key={chip.service} className="flex flex-col">
            <Link to={chip.to} className={`chip ${isActive(chip.to) ? "active" : ""}`}>
              <chip.icon className="w-5 h-5" />
              <div className="flex-1">
                <div className="text-sm">{chip.label}</div>
                {chip.hint && (
                  <div className="text-[11px] text-dim">{chip.hint}</div>
                )}
              </div>
            </Link>
            {chip.subItems?.map((s) => (
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
            <Link to="/admin" className={`chip ${isActive("/admin") ? "active" : ""}`}>
              <ShieldCheck className="w-5 h-5 text-warn" />
              <div className="flex-1">
                <div className="text-sm">Администрирование</div>
                <div className="text-[11px] text-dim">
                  {persona.dept_id ? "в рамках депа" : "глобально"}
                </div>
              </div>
            </Link>
          </div>
        )}

        <div className="p-2 border-t border-token flex flex-col gap-2">
          <Link
            to="/me"
            title="Личный кабинет"
            className={`surface-2 border border-token rounded px-2 py-1.5 flex items-center gap-2 text-sm hover-bg transition-colors ${
              isActive("/me") ? "active" : ""
            }`}
          >
            <div className="w-7 h-7 rounded-full bg-accent flex items-center justify-center text-xs font-semibold shrink-0">
              {persona.initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="leading-tight truncate">{persona.username}</div>
              <div className="text-[10px] text-dim leading-tight truncate" title={roleLine}>
                {roleLine}
              </div>
            </div>
            <UserCog className="w-4 h-4 text-dim shrink-0" aria-hidden="true" />
          </Link>

          <ThemeSwitcher />

          <button
            type="button"
            onClick={onLogout}
            className="btn flex items-center justify-center gap-1.5"
          >
            <LogOut className="w-4 h-4" />
            Logout
          </button>
        </div>
      </div>
    </aside>
  );
}
