/**
 * Backward-compat shim around AuthContext.
 *
 * The codebase (~108 call-sites) still imports `usePersona()` for ACL hints
 * and for rendering the active actor in chrome. Rather than touch every
 * caller, this file translates the real `IdentityContext` returned by
 * `auth_service` into the legacy `Persona` shape.
 *
 * Mode is governed by `VITE_USE_MOCK_AUTH`:
 *   - `true`  → the persona picker is live; `setPersona(id)` swaps the
 *               mock in `src/mocks/personas.ts` and persists the choice in
 *               localStorage. AuthContext stays unauthenticated.
 *   - `false` → `usePersona()` returns whatever `AuthContext.user` is. If
 *               no one is logged in (initial bootstrap, or unauth flow),
 *               a stable dev fallback persona is returned so existing
 *               render paths never crash on `persona.xyz`.
 *               `setPersona` is a no-op in this mode.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type {
  Persona,
  PersonaId,
  PlatformRole,
  ServiceName,
  ServiceRole,
} from "@/types/persona";
import {
  DEFAULT_PERSONA_ID,
  PERSONAS,
  personaById,
} from "@/mocks/personas";
import { USE_MOCK_AUTH, useAuthOptional } from "@/contexts/AuthContext";
import type { IdentityContext } from "@/api/auth/types";

const STORAGE_KEY = "dbos-persona";

// Real-mode pre-auth sentinel. Empty accessible_services + no admin so any
// RouteGuard check (should it ever run before the splash/login redirect)
// fails closed.
const EMPTY_PERSONA: Persona = {
  id: "anonymous" as PersonaId,
  username: "",
  email: "",
  initials: "",
  display_name: "",
  dept_id: null,
  platform_role: null,
  service_roles: {},
  accessible_services: [],
  has_admin: false,
  tagline: "",
};

interface PersonaContextValue {
  persona: Persona;
  setPersona: (id: PersonaId) => void;
  allPersonas: Persona[];
}

const PersonaContext = createContext<PersonaContextValue | undefined>(undefined);

function readStoredMockId(): PersonaId {
  if (typeof window === "undefined") return DEFAULT_PERSONA_ID;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && personaById(stored)) return stored as PersonaId;
  } catch {
    // ignore
  }
  return DEFAULT_PERSONA_ID;
}

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [mockId, setMockId] = useState<PersonaId>(readStoredMockId);
  const auth = useAuthOptional();

  const setPersona = useCallback((id: PersonaId) => {
    if (!USE_MOCK_AUTH) return;
    setMockId(id);
    // Не пишем выбор в localStorage: persona-picker — dev-инструмент,
    // в проде disabled. Перезагрузка вкладки = снова дефолт. Тесты
    // могут предзаполнить localStorage напрямую (см. HomeVariants.test).
  }, []);

  const persona = useMemo<Persona>(() => {
    if (USE_MOCK_AUTH) {
      return personaById(mockId) ?? PERSONAS[0];
    }
    if (auth?.user) return identityToPersona(auth.user);
    // Pre-auth render path in real mode (login screen, bootstrap splash, or
    // post-logout). Return an empty no-access shape — RouteGuard splash/login
    // redirect must run before any guarded page reads from this. Components
    // outside guards (Login, splash) only touch presentational fields.
    return EMPTY_PERSONA;
  }, [mockId, auth?.user]);

  const value = useMemo<PersonaContextValue>(
    () => ({ persona, setPersona, allPersonas: PERSONAS }),
    [persona, setPersona],
  );

  return (
    <PersonaContext.Provider value={value}>{children}</PersonaContext.Provider>
  );
}

export function usePersona() {
  const ctx = useContext(PersonaContext);
  if (!ctx) throw new Error("usePersona must be used inside <PersonaProvider>");
  return ctx;
}

// ---------------------------------------------------------------------------
// IdentityContext → Persona adapter
// ---------------------------------------------------------------------------

const KNOWN_UI_SERVICES: ServiceName[] = [
  "auth",
  "secret",
  "server",
  "worker",
  "logging",
  "config",
];

const SERVICE_BACKEND_TO_UI: Record<string, ServiceName> = {
  auth_service: "auth",
  secret_service: "secret",
  server_service: "server",
  worker_service: "worker",
  loging_service: "logging",
  logging_service: "logging",
  config_service: "config",
};

function toUiService(name: string): ServiceName | null {
  if (SERVICE_BACKEND_TO_UI[name]) return SERVICE_BACKEND_TO_UI[name];
  if ((KNOWN_UI_SERVICES as string[]).includes(name)) return name as ServiceName;
  return null;
}

function initialsOf(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "??";
  const parts = trimmed.split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase();
}

function pickServiceRole(roles: string[] | undefined): ServiceRole | null {
  if (!roles?.length) return null;
  if (roles.includes("admin")) return "admin";
  if (roles.includes("operator")) return "operator";
  if (roles.includes("reader")) return "reader";
  return null;
}

/**
 * Сводит backend-написание platform_role к каноническому UI-набору.
 * Backend отдаёт `department_admin` и legacy `loging_admin` / `loging_reader`;
 * UI ждёт `dep_admin` / `logging_admin` / `logging_reader`. Незнакомые значения
 * (включая обычного юзера без роли) → null.
 */
function normalizePlatformRole(raw: string | null | undefined): PlatformRole {
  switch (raw) {
    case "account_admin":
      return "account_admin";
    case "dep_admin":
    case "department_admin":
      return "dep_admin";
    case "logging_admin":
    case "loging_admin":
      return "logging_admin";
    case "logging_reader":
    case "loging_reader":
      return "logging_reader";
    case "logging_reader_dep":
    case "loging_reader_dep":
      return "logging_reader_dep";
    default:
      return null;
  }
}

function identityToPersona(me: IdentityContext): Persona {
  const username = me.username ?? "unknown";
  // Backend emits `department_admin` и legacy-написание `loging_*`; UI оперирует
  // каноническими `dep_admin` / `logging_*`. Нормализуем на чтении — иначе
  // dep_admin/logging-роли схлопываются в null и теряют весь RBAC.
  const platformRole = normalizePlatformRole(me.platform_role ?? null);

  const serviceRoles: Partial<Record<ServiceName, ServiceRole>> = {};
  for (const [svc, roles] of Object.entries(me.service_roles ?? {})) {
    const ui = toUiService(svc);
    if (!ui) continue;
    const role = pickServiceRole(roles);
    if (role) serviceRoles[ui] = role;
  }

  const accessibleSet = new Set<ServiceName>();
  for (const svc of me.allowed_services ?? []) {
    const ui = toUiService(svc);
    if (ui) accessibleSet.add(ui);
  }
  // Platform roles imply broad access regardless of allowed_services.
  if (platformRole === "account_admin") {
    for (const s of KNOWN_UI_SERVICES) accessibleSet.add(s);
  } else if (
    platformRole === "logging_admin" ||
    platformRole === "logging_reader"
  ) {
    accessibleSet.add("logging");
    accessibleSet.add("config");
  } else if (platformRole === "logging_reader_dep") {
    // Dept-scoped audit reader: backend ограничивает выдачу его отделом, но в UI
    // ему нужен сам раздел /log. config-чип ему не положен — он не платформенный.
    accessibleSet.add("logging");
  }

  const hasAdmin =
    me.has_admin === true ||
    platformRole === "account_admin" ||
    platformRole === "dep_admin" ||
    platformRole === "logging_admin" ||
    Object.values(serviceRoles).some((r) => r === "admin");

  return {
    id: (me.user_id ?? username) as PersonaId,
    username,
    email: me.email ?? `${username}@dbos.local`,
    initials: initialsOf(username),
    display_name: username,
    dept_id: (me.department_id ?? null) as Persona["dept_id"],
    platform_role: platformRole,
    service_roles: serviceRoles,
    accessible_services: [...accessibleSet],
    has_admin: hasAdmin,
    tagline: "",
  };
}
