import { useMemo, type MouseEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  LogOut,
  UserCog,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  BookOpen,
  FileText,
} from "lucide-react";
import { useAuthOptional } from "@/contexts/AuthContext";
import { usePersona } from "@/contexts/PersonaContext";
import { useDeptLabelOpt } from "@/lib/labels";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { listServices } from "@/api/auth/services";
import type { Service } from "@/api/auth/types";
import {
  buildAdminItems,
  visibleItems,
  type AdminBlock,
  type AdminItem,
} from "@/pages/admin/adminCatalog";
import { ThemeSwitcher } from "./ThemeSwitcher";
import { NotificationBell } from "@/components/notifications/NotificationBell";

interface AdminOnlyPanelProps {
  width: number;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

const BLOCK_TITLES: Record<AdminBlock, string> = {
  services: "Сервисы и пользователи",
  cluster: "Кластер",
};

// Mock-режим — backend не поднят, но динамические «Роли · …» должны быть видны
// для скриншотов/e2e. Список совпадает с AdminHub.tsx.
const MOCK_SERVICES: Service[] = [
  { service_name: "server_service", description: null, is_active: true, created_at: "2026-01-01T00:00:00Z" },
  { service_name: "secret_service", description: null, is_active: true, created_at: "2026-01-01T00:00:00Z" },
  { service_name: "loging_service", description: null, is_active: true, created_at: "2026-01-01T00:00:00Z" },
];

function groupByBlockAndSection(items: AdminItem[]): Array<{
  block: AdminBlock;
  sections: Array<{ group: string; items: AdminItem[] }>;
}> {
  const byBlock = new Map<AdminBlock, Map<string, AdminItem[]>>();
  for (const it of items) {
    let bm = byBlock.get(it.block);
    if (!bm) {
      bm = new Map<string, AdminItem[]>();
      byBlock.set(it.block, bm);
    }
    const key = it.group ?? "";
    const arr = bm.get(key);
    if (arr) arr.push(it);
    else bm.set(key, [it]);
  }
  // Order: services first, cluster second (как в AdminMiddle).
  const order: AdminBlock[] = ["services", "cluster"];
  const out: Array<{
    block: AdminBlock;
    sections: Array<{ group: string; items: AdminItem[] }>;
  }> = [];
  for (const block of order) {
    const bm = byBlock.get(block);
    if (!bm) continue;
    const sections = [...bm.entries()].map(([group, list]) => ({
      group,
      items: list,
    }));
    out.push({ block, sections });
  }
  return out;
}

/**
 * Минималистичная левая панель только для account_admin (и в перспективе —
 * любой админ-only роли). Содержимое /admin превращено в основной навигатор:
 * группировка по block → group, ссылки прямо на `/admin/<id>`. «Главная» и
 * сервисы-chips спрятаны — для account_admin это всё лишний шум.
 */
export function AdminOnlyPanel({ width, collapsed, onToggleCollapsed }: AdminOnlyPanelProps) {
  const { persona } = usePersona();
  const auth = useAuthOptional();
  const location = useLocation();
  const mockMode = useMockMode();

  const servicesQ = useQuery<Service[]>(() => listServices(), [], {
    enabled: !mockMode,
  });

  const items = useMemo(() => {
    const services: Service[] = mockMode ? MOCK_SERVICES : servicesQ.data ?? [];
    return visibleItems(buildAdminItems(services), persona);
  }, [mockMode, servicesQ.data, persona]);

  const grouped = useMemo(() => groupByBlockAndSection(items), [items]);

  const onLogout = async (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (auth) await auth.logout();
  };

  const isActive = (to: string) =>
    location.pathname === to || location.pathname.startsWith(to + "/");

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
      <div className="p-2 shrink-0">
        <Link
          to="/admin"
          title={collapsed ? "Администрирование" : undefined}
          className={`chip ${location.pathname === "/admin" ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
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

      <nav className="flex-1 min-h-0 overflow-y-auto px-2 pb-2 flex flex-col gap-1">
        {grouped.length === 0 && !collapsed && (
          <div className="text-xs text-dim px-2 py-3 text-center">
            Нет доступных разделов.
          </div>
        )}
        {grouped.map(({ block, sections }) => (
          <div key={block} className="flex flex-col">
            {!collapsed && (
              <div className="px-2 pt-3 pb-1 text-[11px] text-dim uppercase tracking-wider">
                {BLOCK_TITLES[block]}
              </div>
            )}
            {sections.map(({ group, items: list }) => (
              <div key={`${block}:${group}`} className="flex flex-col">
                {!collapsed && group && (
                  <div className="px-2 pt-1 text-[10px] text-dim opacity-70 uppercase tracking-wider">
                    {group}
                  </div>
                )}
                {list.map((it) => {
                  const to = `/admin/${it.id}`;
                  const Icon = it.icon;
                  return (
                    <Link
                      key={it.id}
                      to={to}
                      title={collapsed ? it.label : it.hint}
                      className={`chip ${isActive(to) ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
                    >
                      <Icon className="w-4 h-4 shrink-0" />
                      {!collapsed && (
                        <div className="flex-1 min-w-0">
                          <div className="text-sm truncate">{it.label}</div>
                          {it.hint && (
                            <div className="text-[11px] text-dim truncate">
                              {it.hint}
                            </div>
                          )}
                        </div>
                      )}
                    </Link>
                  );
                })}
              </div>
            ))}
          </div>
        ))}
      </nav>

      <div className="mt-auto shrink-0 flex flex-col">
        <div className="p-2 border-t border-token">
          <Link
            to="/log"
            title={collapsed ? "Журнал аудита (только чтение)" : undefined}
            className={`chip ${isActive("/log") ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
          >
            <FileText className="w-5 h-5 text-accent shrink-0" />
            {!collapsed && (
              <div className="flex-1">
                <div className="text-sm">Журнал</div>
                <div className="text-[11px] text-dim">аудит · только чтение</div>
              </div>
            )}
          </Link>
        </div>
        <div className="p-2 border-t border-token">
          <Link
            to="/wiki"
            title={collapsed ? "Вики — примеры API" : undefined}
            className={`chip ${isActive("/wiki") ? "active" : ""} ${collapsed ? "justify-center" : ""}`}
          >
            <BookOpen className="w-5 h-5 text-accent shrink-0" />
            {!collapsed && <div className="flex-1 text-sm">Вики</div>}
          </Link>
        </div>
        <div className="p-2 border-t border-token flex flex-col gap-2">
          <Link
            to="/me"
            title={collapsed ? `${persona.username} — личный кабинет` : "Личный кабинет"}
            className={`surface-2 border border-token rounded px-2 py-1.5 flex items-center gap-2 text-sm hover-bg transition-colors ${
              location.pathname === "/me" ? "active" : ""
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
