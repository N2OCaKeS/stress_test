import { ApiError, apiErrMsg } from "@/api/client";
import type { ServerAccount } from "@/api/server/types";
import type { Persona } from "@/types/persona";

// Парольная политика ручного ввода для server-аккаунтов и IPMI-кредов —
// зеркало `server_service/src/core/password_policy.py` (базовая политика):
//   минимум 8 символов, обязательны и буквы, и цифры.
// Отличается от auth_service (`@/lib/passwordPolicy`, ≥12), поэтому живёт
// отдельно. Короткий хинт вешаем у поля, полный текст — в ошибке и в маппинге
// backend'ового VALIDATION_ERROR по `password_b64`.
export const PASSWORD_POLICY_HINT = "минимум 8 символов, буквы и цифры";

// Усиленная политика для bootstrap-кред на `server.prepare` — зеркало
// `server_service/src/core/password_policy.py::validate_strong_password`:
//   минимум 16 символов, обязательны буква, цифра и спецсимвол. Это входная
//   точка доступа к свежей коробке, слабый пароль здесь backend отобьёт (422).
export const PASSWORD_POLICY_HINT_STRONG =
  "минимум 16 символов, минимум одна буква, одна цифра и один спецсимвол";

const PASSWORD_POLICY_TEXT = `Пароль не соответствует политике: ${PASSWORD_POLICY_HINT}.`;

/**
 * Клиентская проверка пароля по базовой политике server_service. Пустую строку
 * проверять не нужно — пароль на create/rotate опционален, backend сам сгенерит
 * случайный. Вызывать только когда пароль реально введён.
 */
export function validateAccountPassword(value: string): string | null {
  if (value.length < 8) return PASSWORD_POLICY_TEXT;
  if (!/[A-Za-z]/.test(value) || !/[0-9]/.test(value)) {
    return PASSWORD_POLICY_TEXT;
  }
  return null;
}

/**
 * Маппит backend-VALIDATION_ERROR по полю `password_b64` (в т.ч. тип
 * `WEAK_PASSWORD`) в человекочитаемый текст парольной политики. Не про пароль —
 * возвращает null, чтобы caller отдал ошибку дальше своему обработчику.
 */
export function accountPasswordPolicyError(e: unknown): string | null {
  if (e instanceof ApiError && /password_b64/i.test(apiErrMsg(e, ""))) {
    return PASSWORD_POLICY_TEXT;
  }
  return null;
}

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

/**
 * Человекочитаемая причина отказа в массовом prepare-batch
 * (`ServerBatchFailed.reason`). Незнакомый код отдаём как есть, чтобы новый
 * backend-вариант не терялся.
 */
const PREPARE_BATCH_REASON_RU: Record<string, string> = {
  not_found_or_cross_dept: "сервер не найден или принадлежит другому отделу",
  decommissioned: "сервер выведен из эксплуатации",
  idempotent_conflict: "уже выполняется такая же задача (idempotency)",
  idempotency_key_reuse_conflict:
    "повторное использование idempotency-ключа — задача не поставлена",
  account_has_no_password: "у привязанной учётки нет сохранённого пароля",
  account_not_found: "выбранная учётка не найдена",
  account_not_linked: "учётка не привязана к этому серверу",
  permission_denied: "недостаточно прав на prepare этого сервера",
  worker_unreachable: "worker недоступен — задача не поставлена",
  not_attempted: "не пытались (батч прерван после ошибки worker'а)",
};

export function prepareBatchReasonRu(reason: string): string {
  return PREPARE_BATCH_REASON_RU[reason] ?? reason;
}

/**
 * Человекочитаемая причина per-action итога clean'а (`ServerCleanActionResult`).
 * Покрывает `skipped`/`failed`-коды backend'а; незнакомое отдаём как есть.
 */
const CLEAN_REASON_RU: Record<string, string> = {
  not_selected: "действие не выбрано",
  not_found_or_cross_dept: "сервер не найден или принадлежит другому отделу",
  decommissioned: "сервер выведен из эксплуатации",
  prepare_required: "сначала нужен prepare — сервер не подготовлен",
  worker_unreachable: "worker недоступен — задача не поставлена",
  idempotent_conflict: "уже выполняется такая же задача (idempotency)",
  no_linked_accounts: "у сервера нет привязанных учёток",
  permission_denied: "недостаточно прав на это действие",
  not_attempted: "не выполнялось (предыдущее действие прервало clean)",
};

export function cleanReasonRu(reason: string): string {
  return CLEAN_REASON_RU[reason] ?? reason;
}
