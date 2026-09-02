/**
 * Сборка отображаемого имени пользователя из ФИО.
 *
 * auth_service отдаёт `last_name` / `first_name` / `middle_name` (любое поле
 * может быть пустым) в `/me`, `/users` и introspect. В UI пользователя везде,
 * где это уместно, показываем ФИО, а не технический login. Если ни одно поле
 * ФИО не заполнено — падаем на login/username, а в самом крайнем случае на "—".
 */

export interface FioParts {
  last_name?: string | null;
  first_name?: string | null;
  middle_name?: string | null;
}

export interface FioFallback {
  username?: string | null;
  login?: string | null;
}

export type FioInput = FioParts & FioFallback;

function clean(s: string | null | undefined): string {
  return (s ?? "").trim();
}

/** «Фамилия Имя Отчество» из непустых частей (пустые опускаются). */
export function fullFio(u: FioParts | null | undefined): string {
  if (!u) return "";
  return [clean(u.last_name), clean(u.first_name), clean(u.middle_name)]
    .filter(Boolean)
    .join(" ");
}

/** true, если заполнена хотя бы одна часть ФИО. */
export function hasFio(u: FioParts | null | undefined): boolean {
  return fullFio(u).length > 0;
}

function fallbackLogin(u: FioInput | null | undefined): string {
  if (!u) return "";
  return clean(u.username) || clean(u.login);
}

interface FioOpts {
  /** Чем заменить отсутствующее ФИО (по умолчанию — login/username). */
  fallback?: string;
  /** Что показать, когда нет ни ФИО, ни login (по умолчанию "—"). */
  empty?: string;
}

/**
 * Полное имя для отображения: ФИО, иначе login/username, иначе "—".
 */
export function formatFio(u: FioInput | null | undefined, opts?: FioOpts): string {
  const fio = fullFio(u);
  if (fio) return fio;
  const fb = opts?.fallback ?? fallbackLogin(u);
  return fb || (opts?.empty ?? "—");
}

/**
 * Краткая форма «Фамилия И.О.» для тесных мест (строки списков, чипы).
 * Нет фамилии, но есть имя — вернёт имя (при наличии отчества — «Имя О.»).
 * Пустое ФИО — фолбэк на login/username, иначе "—".
 */
export function formatFioShort(
  u: FioInput | null | undefined,
  opts?: FioOpts,
): string {
  const last = clean(u?.last_name);
  const first = clean(u?.first_name);
  const middle = clean(u?.middle_name);
  const initials = [first, middle]
    .filter(Boolean)
    .map((p) => `${p[0].toUpperCase()}.`)
    .join("");

  let short = "";
  if (last) short = initials ? `${last} ${initials}` : last;
  else if (first) short = middle ? `${first} ${middle[0].toUpperCase()}.` : first;

  if (short) return short;
  const fb = opts?.fallback ?? fallbackLogin(u);
  return fb || (opts?.empty ?? "—");
}
