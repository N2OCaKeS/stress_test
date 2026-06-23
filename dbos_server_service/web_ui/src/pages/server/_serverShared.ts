import { ApiError, apiErrMsg } from "@/api/client";
import type { ServerAccount } from "@/api/server/types";
import type { Persona } from "@/types/persona";

/**
 * Человекочитаемое сообщение по ошибке деструктивной операции над сервером.
 *
 * Бронь сервера (busy-lease) защищает чужую занятую машину: backend отбивает
 * деструктив (delete и т.п.) кодом 409 `SERVER_RESERVED`. Разворачиваем его в
 * понятный текст про бронь; остальное отдаём обычным envelope'ом.
 */
export function reservedErrorMessage(e: unknown, fallback: string): string {
  if (
    e instanceof ApiError &&
    e.status === 409 &&
    e.errorCode === "SERVER_RESERVED"
  ) {
    return "Сервер забронирован другим пользователем — снимите бронь или дождитесь её снятия, чтобы выполнить операцию.";
  }
  return apiErrMsg(e, fallback);
}

/**
 * Аккаунты сервера, видимые текущей persona — грубый, но честный client-side
 * фильтр для picker'а. Отражает ту же логику, что backend для action `view`:
 *
 *   - `server.admin` — все аккаунты;
 *   - `dep_admin` своего dept'а — все аккаунты его dept'а;
 *   - `server.operator` / `server.reader` — только аккаунты своего dept'а
 *     (cross-dep шаринг через DeptGrant в UI пока не виден, backend отрежет);
 *   - account_admin / logging_admin сюда не попадают — server_service для них
 *     закрыт целиком, страница /server не рендерится.
 *
 * Источник истины при реальном fetch'е пароля / открытии сессии — backend
 * (полная проверка + 403, если grant'а нет). Здесь только UX, чтобы не
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
