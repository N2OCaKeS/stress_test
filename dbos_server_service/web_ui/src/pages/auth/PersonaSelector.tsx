import { Link, useNavigate } from "react-router-dom";
import {
  Grid3x3,
  Crown,
  UsersRound,
  FileText,
  Eye,
  Lock,
  Server,
  Building2,
  Users,
  Cog,
  ShieldCheck,
  Filter,
  Trash,
  type LucideIcon,
} from "lucide-react";
import { usePersona } from "@/contexts/PersonaContext";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";
import { useDeptLabel } from "@/lib/labels";
import type { Persona } from "@/types/persona";

const SERVICE_CHIPS: Record<
  string,
  { icon: LucideIcon; label: string }
> = {
  auth: { icon: Users, label: "Пользователи" },
  server: { icon: Server, label: "Серверы" },
  secret: { icon: Lock, label: "Секреты" },
  worker: { icon: Cog, label: "Workers" },
  logging: { icon: FileText, label: "Аудит" },
  config: { icon: Cog, label: "Config" },
};

const ROLE_ICONS: Record<string, LucideIcon> = {
  account_admin: Crown,
  dep_admin: UsersRound,
  logging_admin: FileText,
  logging_reader: Eye,
};

function PersonaTile({
  persona,
  onPick,
}: {
  persona: Persona;
  onPick: (p: Persona) => void;
}) {
  const RoleIcon = persona.platform_role
    ? ROLE_ICONS[persona.platform_role]
    : null;
  const serviceRoleEntries = Object.entries(persona.service_roles);
  const deptLabel = useDeptLabel(persona.dept_id);

  return (
    <button
      type="button"
      onClick={() => onPick(persona)}
      className="persona-tile text-left"
    >
      <div className="flex items-start gap-4">
        <div className="persona-avatar">{persona.initials}</div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-lg">{persona.username}</div>
          <div className="text-xs text-dim mono">{persona.email}</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {persona.platform_role && RoleIcon && (
              <span className="role-badge platform">
                <RoleIcon className="w-3 h-3" />
                {persona.platform_role}
              </span>
            )}
            {serviceRoleEntries.map(([svc, role]) => {
              const meta = SERVICE_CHIPS[svc];
              const Icon = meta?.icon ?? Lock;
              return (
                <span key={svc} className="role-badge service">
                  <Icon className="w-3 h-3" />
                  {svc}_{role}
                </span>
              );
            })}
            {persona.dept_id && (
              <span className="role-badge">
                <Building2 className="w-3 h-3" />
                {deptLabel}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="text-xs text-dim border-t border-token pt-3">
        <div className="mb-1">Видит:</div>
        <div className="flex flex-wrap gap-1.5">
          {persona.accessible_services.map((s) => {
            const meta = SERVICE_CHIPS[s];
            const Icon = meta?.icon ?? Cog;
            return (
              <span key={s} className="chip-small">
                <Icon className="w-3 h-3" />
                {meta?.label ?? s}
              </span>
            );
          })}
          {persona.id === "carol" && (
            <>
              <span className="chip-small">
                <Filter className="w-3 h-3" />
                Правила
              </span>
              <span className="chip-small">
                <Trash className="w-3 h-3" />
                Хранение
              </span>
            </>
          )}
          {persona.has_admin && (
            <span className="chip-small">
              <ShieldCheck className="w-3 h-3" />
              Admin
            </span>
          )}
          {persona.id === "dave" && (
            <span className="chip-small text-warn">Только чтение</span>
          )}
        </div>
        <div className="mt-2 text-[11px] text-dim">{persona.tagline}</div>
      </div>
    </button>
  );
}

export function PersonaSelector() {
  const { allPersonas, setPersona } = usePersona();
  const navigate = useNavigate();

  const pick = (p: Persona) => {
    setPersona(p.id);
    navigate("/home");
  };

  return (
    <div className="gradient-bg min-h-screen flex flex-col">
      <header className="border-b border-token surface px-6 h-12 flex items-center gap-3">
        <Grid3x3 className="w-4 h-4 text-accent" />
        <span className="text-sm font-semibold">EMM</span>
        <span className="text-xs text-dim ml-2">UI mockup · фаза 2</span>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs text-dim">Тема:</label>
          <ThemeSwitcher />
        </div>
      </header>

      <main className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-6xl">
          <div className="text-center mb-10">
            <h1 className="text-4xl font-bold mb-3">Войдите как...</h1>
            <p className="text-dim">
              Mockup-режим. Выбери персону, чтобы посмотреть UI с её правами и
              доступными сервисами.
            </p>
            <p className="text-xs text-dim mt-2">
              В боевом режиме это будет обычный экран входа{" "}
              <span className="mono">POST /api/auth/v1/login</span>.
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {allPersonas.map((p) => (
              <PersonaTile key={p.id} persona={p} onPick={pick} />
            ))}
          </div>

          <div className="text-center mt-10 text-xs text-dim">
            <span className="mono">ℹ</span> В боевом режиме персоны не
            выбирают — оператор логинится своим паролем. Эти плитки — для
            полировки UI с разными ACL-комбинациями.
          </div>

          <div className="text-center mt-6">
            <Link to="/login" className="text-xs text-accent">
              → Перейти к боевому экрану входа
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
