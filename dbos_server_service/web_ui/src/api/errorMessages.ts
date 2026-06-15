/**
 * Человекочитаемые подсказки для стабильных `error_code` бэкенда.
 *
 * Бэкенд отдаёт единый конверт `{ error, error_code, message, details, ... }`
 * (см. `ApiError` в `@/api/client`). Поле `message` уже на русском, но часть
 * кодов несёт нетривиальный контекст («почему так и что делать»), который
 * стоит показать пользователю явно, а не прятать за generic `CODE: message`.
 *
 * Каталог кодов — `API_ENDPOINTS.md` каждого сервиса. Здесь собраны топовые
 * actionable-коды для потоков create / delete / reveal / ban / rotate / grant /
 * transfer / assign. Остальные коды деградируют к `message` из конверта.
 *
 * Используется из `apiErrMsg` (`@/api/client`): если код есть в таблице —
 * берём подсказку отсюда, иначе — `error_code: message` как раньше.
 */

import type { ApiError } from "@/api/client";

/**
 * code → готовая подсказка. Текст самодостаточный: что произошло и что делать.
 * Без подстановки message бэкенда (он часто дублирует или менее конкретен).
 */
export const HUMAN_MESSAGES: Record<string, string> = {
  // --- 409 конфликты: дубли имён --------------------------------------------
  USER_ALREADY_EXISTS: "Пользователь с таким логином уже есть. Выберите другой.",
  DEPARTMENT_ALREADY_EXISTS: "Департамент с таким именем уже существует.",
  GROUP_ALREADY_EXISTS: "Группа с таким именем уже существует в департаменте.",
  BOT_NAME_TAKEN: "Имя бота уже занято в этом департаменте.",
  TOKEN_NAME_ALREADY_EXISTS: "У вас уже есть токен с таким именем.",
  SERVICE_ALREADY_EXISTS: "Сервис с таким именем уже зарегистрирован.",
  SERVICE_ROLE_ALREADY_EXISTS:
    "Роль с таким именем уже есть для этого сервиса в департаменте.",
  OAUTH_CLIENT_NAME_EXISTS: "OAuth-клиент с таким именем уже зарегистрирован.",
  SERVER_DUPLICATE: "Сервер с такими реквизитами уже заведён.",
  IPMI_DUPLICATE: "IPMI-контроллер с такими реквизитами уже привязан.",
  NAME_DUPLICATE: "Запись с таким именем уже существует.",
  ACCOUNT_DUPLICATE:
    "Учётная запись с таким логином уже есть на одном из серверов.",
  DEPT_GRANT_DUPLICATE: "Доступ этому департаменту уже выдан.",
  ROLE_ACL_DUPLICATE: "Правило доступа для этой роли уже существует.",
  PERMISSION_ALREADY_EXISTS: "Такое право уже выдано.",
  RULE_NAME_CONFLICT: "Правило с таким именем уже есть.",
  ALREADY_GROUP_MEMBER: "Пользователь уже состоит в этой группе.",
  GROUP_SERVICE_ALREADY_GRANTED: "Доступ к сервису группе уже выдан.",
  SERVICE_ALREADY_GRANTED: "Доступ к сервису департаменту уже выдан.",

  // --- 409 конфликты: «уже сделано» / состояние -----------------------------
  TOKEN_ALREADY_REVOKED: "Токен уже отозван.",
  BOT_TOKEN_ALREADY_REVOKED: "Токен бота уже отозван.",
  BAN_ALREADY_ACTIVE: "Пользователь уже забанен.",
  CREDENTIALS_ALREADY_APPLIED: "Учётные данные уже применены на сервере.",
  CREDENTIAL_BLOCKED:
    "Секрет заблокирован (compromise). Сначала восстановите его (recover).",
  CREDENTIAL_NOT_BLOCKED: "Секрет не заблокирован — восстанавливать нечего.",
  SERVER_ALREADY_BUSY: "Сервер занят другой операцией. Дождитесь завершения.",
  SERVER_NOT_BUSY: "На сервере нет активной операции для отмены.",
  TASK_NOT_CANCELLABLE: "Задача в финальном состоянии — отменить нельзя.",
  IDEMPOTENCY_KEY_REUSE_CONFLICT:
    "Этот ключ идемпотентности уже использован с другими параметрами.",
  IDEMPOTENCY_KEY_CONFLICT:
    "Этот ключ идемпотентности уже использован с другими параметрами.",
  TASK_IDEMPOTENT_CONFLICT:
    "Этот ключ идемпотентности уже использован с другими параметрами.",
  OS_VERSION_IN_USE: "Версия ОС используется серверами — удалить нельзя.",

  // --- 409: «последний / нельзя удалить себя» -------------------------------
  LAST_ACCOUNT_ADMIN:
    "Нельзя снять роль с последнего account_admin — платформа останется без администратора.",
  USERS_REMAIN_IN_DEPT:
    "В департаменте ещё есть пользователи. Переназначьте или удалите их перед удалением департамента.",
  DEPT_GRANT_RECIPIENT_IS_OWNER:
    "Этот департамент уже владеет секретом — отдельный grant не нужен.",

  // --- 422 валидация --------------------------------------------------------
  SAME_PASSWORD: "Новый пароль совпадает со старым — придумайте другой.",
  WEAK_PASSWORD: "Пароль слишком слабый. Усложните его.",
  INVALID_STATUS_FILTER: "Недопустимое значение фильтра по статусу.",
  INVALID_OS_VERSION: "Некорректная версия ОС.",
  INVALID_TRANSFER_TARGET: "Недопустимый получатель для передачи.",
  PLAINTEXT_TOO_LARGE: "Значение секрета превышает лимит размера.",
  MISSING_REQUIRED_FIELD: "Не заполнено обязательное поле.",

  // --- 403 RBAC / контекст --------------------------------------------------
  PASSWORD_CHANGE_REQUIRED:
    "Требуется смена пароля — продолжите после обновления пароля.",
  CANNOT_BYPASS_PASSWORD_CHANGE:
    "Сначала смените временный пароль, потом выполняйте другие действия.",
  PERMISSION_DENIED: "Недостаточно прав для этого действия.",
  SERVICE_ACCESS_DENIED: "Нет доступа к этому сервису.",
  DEPARTMENT_ACCESS_DENIED: "Действие вне вашего департамента.",
  DEPARTMENT_ISOLATION: "Объект принадлежит другому департаменту.",
  CREDENTIAL_ACCESS_DENIED: "Нет доступа к этому секрету.",
  PLATFORM_ADMIN_BUSINESS_DATA_DENIED:
    "Платформенному администратору закрыт доступ к бизнес-данным департамента.",
  PLATFORM_ROLE_ASSIGNMENT_DENIED:
    "Назначать платформенные роли может только account_admin.",
  STATUS_CHANGE_REQUIRES_ACCOUNT_ADMIN:
    "Менять статус (блокировку) может только account_admin.",
  BOT_CREATION_FORBIDDEN: "Нет прав на создание ботов в этом департаменте.",
  ACCOUNT_ADMIN_REQUIRED: "Действие доступно только account_admin.",
  DEPT_ADMIN_REQUIRED: "Действие доступно только администратору департамента.",
  SERVICE_ADMIN_REQUIRED: "Нужна роль admin в этом сервисе.",
  ROLE_REQUIRED: "Недостаточно прав: для этого действия нужна другая роль.",
  INSUFFICIENT_ROLE: "Недостаточно прав для управления этим сервисом.",
  NO_DEPARTMENT:
    "У роли нет привязки к департаменту — этот scope недоступен.",

  // --- 404 не найдено -------------------------------------------------------
  USER_NOT_FOUND: "Пользователь не найден.",
  DEPARTMENT_NOT_FOUND: "Департамент не найден.",
  GROUP_NOT_FOUND: "Группа не найдена.",
  BOT_NOT_FOUND: "Бот не найден.",
  TOKEN_NOT_FOUND: "Токен не найден.",
  BAN_NOT_FOUND: "Активная блокировка пользователя не найдена.",
  SERVER_NOT_FOUND: "Сервер не найден.",
  OS_VERSION_NOT_FOUND: "Версия ОС не найдена.",
  TASK_NOT_FOUND: "Задача не найдена.",
  CREDENTIAL_NOT_FOUND: "Секрет не найден.",
  USER_ACL_NOT_FOUND: "Доступ пользователя не найден.",
  RULE_NOT_FOUND: "Правило не найдено.",

  // --- user-ACL шаринга секрета ---------------------------------------------
  USER_ACL_DUPLICATE: "У этого пользователя уже есть доступ к секрету.",
  USER_ACL_OWNER_REDUNDANT:
    "Владелец и так имеет полный доступ — отдельный доступ не нужен.",
  USER_ACL_SELF_REDUNDANT:
    "Нельзя выдать доступ самому себе.",

  // --- специфичные подсказки server_account ---------------------------------
  ACCOUNT_REQUIRED:
    "Сначала привяжите учётную запись (account) к серверу.",
  ACCOUNT_NOT_LINKED:
    "Учётная запись не привязана к этому серверу.",
  ACCOUNT_NOT_FOUND: "Учётная запись не найдена.",
  ACCOUNT_HAS_NO_PASSWORD: "У учётной записи нет пароля для показа.",
  ACCOUNT_NO_SERVERS: "К учётной записи не привязан ни один сервер.",
  NO_LINKED_SERVERS: "Нет привязанных серверов для этой операции.",
  NO_IPMI_CONTROLLER: "К серверу не привязан IPMI-контроллер.",
  DECRYPT_FAILED:
    "Не удалось расшифровать секрет (ключ шифрования сменился?).",

  // --- 429 throttle (детали countdown'а — на call-site через retryAfter) ----
  RATE_LIMIT_EXCEEDED: "Слишком много запросов. Повторите позже.",
  ACCOUNT_TEMPORARILY_LOCKED:
    "Аккаунт временно заблокирован после неудачных попыток.",

  // --- 503 downstream -------------------------------------------------------
  LOGING_SERVICE_UNAVAILABLE:
    "Сервис аудита недоступен — повторите позже.",
  AUTH_SERVICE_UNAVAILABLE:
    "Сервис аутентификации недоступен — повторите позже.",
  AUTH_SERVICE_UNREACHABLE:
    "Сервис аутентификации недоступен — повторите позже.",
  AUTH_SERVICE_TIMEOUT:
    "Сервис аутентификации не ответил вовремя — повторите позже.",
  WORKER_UNREACHABLE:
    "Воркер недоступен — операция не запущена, повторите позже.",
  WORKER_REDIS_UNAVAILABLE:
    "Очередь задач недоступна — повторите позже.",
};

/**
 * Извлекает список полей из `details.errors` (pydantic 422 / `VALIDATION_ERROR`).
 * Бэкенд кладёт `details: { errors: [{ loc: [...], msg, type }, ...] }`
 * (см. `validation_exception_handler` в `src/main.py` сервиса). Возвращает
 * человекочитаемые имена полей (последний сегмент `loc`, кроме `body`).
 */
export function validationFields(err: ApiError): string[] {
  const details = err.details;
  if (!details) return [];
  const raw = (details as Record<string, unknown>)["errors"];
  if (!Array.isArray(raw)) return [];
  const fields = new Set<string>();
  for (const issue of raw) {
    if (!issue || typeof issue !== "object") continue;
    const loc = (issue as Record<string, unknown>)["loc"];
    if (!Array.isArray(loc) || loc.length === 0) continue;
    // loc обычно вида ["body", "<field>", ...]; берём последний осмысленный.
    const segs = loc.filter((s) => s !== "body" && s !== "query" && s !== "path");
    const last = segs.length ? segs[segs.length - 1] : loc[loc.length - 1];
    if (typeof last === "string" || typeof last === "number") {
      fields.add(String(last));
    }
  }
  return [...fields];
}

/**
 * Возвращает подсказку для кода, обогащая её контекстом из `details`:
 *   - `VALIDATION_ERROR` → перечисляет проблемные поля;
 *   - 429-коды с `retryAfter` → добавляет «повтор через N сек».
 * Если код незнаком — `null`, чтобы вызывающий мог деградировать к message.
 */
export function humanErrMsg(err: ApiError): string | null {
  // VALIDATION_ERROR несёт поля — собираем динамически.
  if (err.errorCode === "VALIDATION_ERROR") {
    const fields = validationFields(err);
    if (fields.length) {
      return `Проверьте поля: ${fields.join(", ")}.`;
    }
    return "Проверьте корректность введённых данных.";
  }

  const base = HUMAN_MESSAGES[err.errorCode];
  if (!base) return null;

  // Для throttle-кодов добавляем countdown, если бэк прислал retry_after.
  if (err.retryAfter !== undefined && err.retryAfter > 0) {
    const mins = Math.ceil(err.retryAfter / 60);
    return `${base} Повтор через ${mins} мин (${err.retryAfter} сек).`;
  }
  return base;
}
