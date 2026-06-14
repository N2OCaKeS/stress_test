import type { ServerAccount } from "@/api/server/types";
import type { Persona } from "@/types/persona";

/**
 * Аккаунты сервера, видимые текущей persona — грубый client-side фильтр для
 * picker'а. Backend перепроверит при fetch'е пароля; здесь только UX, чтобы не
 * показывать заведомо недоступные строки. Используется во вкладках
 * manage / drift / packages / console.
 */
export function filterAccessibleAccounts(
  accounts: ServerAccount[],
  persona: Persona,
): ServerAccount[] {
  if (persona.service_roles.server === "admin") return accounts;
  if (
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "operator" ||
    persona.service_roles.server === "reader"
  ) {
    if (!persona.dept_id) return [];
    return accounts.filter((a) => a.department_id === persona.dept_id);
  }
  return [];
}
