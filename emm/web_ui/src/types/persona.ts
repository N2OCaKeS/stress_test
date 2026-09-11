export type ThemeName =
  | "vscode-dark"
  | "vscode-light"
  | "dark-orange"
  | "blue";

export type PlatformRole =
  | "account_admin"
  | "dep_admin"
  | "logging_admin"
  | "logging_reader"
  | "logging_reader_dep"
  | null;

export type ServiceName =
  | "auth"
  | "secret"
  | "server"
  | "worker"
  | "logging"
  | "config"
  | "testing";

export type ServiceRole = "admin" | "operator" | "reader";

/**
 * Persona identifier. The mock personas use stable string ids (bob, alice,
 * carol, dave); real auth users get backend UUIDs. Keeping the type as a
 * broad string allows both flows to share the Persona shape.
 */
export type PersonaId = string;

/**
 * Synthetic dept identifier. Stable, ASCII, used in URLs, filters, scope refs.
 * Human-readable name is rendered through `useDeptLabel(dept_id)` из
 * `@/lib/labels` (читает LabelsProvider, backed by listDepartments).
 */
export type DeptId = "core" | "dev" | "infra" | "ops";

export interface Persona {
  id: PersonaId;
  username: string;
  email: string;
  initials: string;
  display_name: string;
  /** ФИО текущего пользователя (из identity). Любое поле может быть пустым —
   *  UI собирает отображаемое имя через `@/lib/fio` с фолбэком на username. */
  last_name?: string | null;
  first_name?: string | null;
  middle_name?: string | null;
  /** Synthetic dept id; null = platform-wide (no dept scope). */
  dept_id: DeptId | null;
  platform_role: PlatformRole;
  service_roles: Partial<Record<ServiceName, ServiceRole>>;
  accessible_services: ServiceName[];
  has_admin: boolean;
  tagline: string;
}
