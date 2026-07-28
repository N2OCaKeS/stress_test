/**
 * Клиентское зеркало настраиваемой парольной политики логина
 * (`auth_service` `/admin/password-policy`). Дефолт — историческое поведение
 * (≥12 символов, буква и цифра). Активная политика подгружается на старте
 * приложения из публичного эндпоинта `GET /api/auth/v1/password-policy`
 * (`setActivePasswordPolicy` вызывается из App), чтобы формы не блокировали
 * пароль, который backend по текущей политике примет (напр. после ослабления
 * account_admin'ом). Enforcement всё равно на backend'е — это лишь пред-проверка.
 */

// Дефолт/фолбэк — для генератора паролей и хинтов до загрузки политики.
export const MIN_PASSWORD_LENGTH = 12;

export const PASSWORD_POLICY_MESSAGE =
  "Минимум 12 символов, минимум одна буква и одна цифра";

// Активная политика. Обновляется `setActivePasswordPolicy` из App на старте.
let _active = {
  minLength: MIN_PASSWORD_LENGTH,
  requireLetter: true,
  requireDigit: true,
};

/** Залить актуальную политику логина (ответ публичного эндпоинта auth). */
export function setActivePasswordPolicy(p: {
  min_length: number;
  require_letter: boolean;
  require_digit: boolean;
}): void {
  _active = {
    minLength: p.min_length,
    requireLetter: p.require_letter,
    requireDigit: p.require_digit,
  };
}

export function validatePassword(value: string): string | null {
  if (value.length < _active.minLength) {
    return `Минимум ${_active.minLength} символов`;
  }
  if (_active.requireLetter && !/[A-Za-zА-Яа-яЁё]/.test(value)) {
    return "Нужна хотя бы одна буква";
  }
  if (_active.requireDigit && !/\d/.test(value)) {
    return "Нужна хотя бы одна цифра";
  }
  return null;
}

const LOWER = "abcdefghijkmnpqrstuvwxyz";
const UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const DIGIT = "23456789";
const SYM = "!@#$%&*-_+=";

function pick(pool: string): string {
  return pool[Math.floor(Math.random() * pool.length)];
}

export function generateInitialPassword(length = 16): string {
  // Гарантируем хотя бы по одному из каждой policy-критичной группы.
  const chars = [pick(LOWER), pick(UPPER), pick(DIGIT), pick(SYM)];
  const all = LOWER + UPPER + DIGIT + SYM;
  while (chars.length < length) chars.push(pick(all));
  // Перемешиваем, чтобы первые 4 символа не были в порядке классов.
  for (let i = chars.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

// Очень либеральная проверка email — только структура `local@domain.tld`
// без пробелов; локальная часть и доменное имя допускают любые видимые
// символы. Корпоративные адреса бывают экзотическими (`.test`, длинные TLD,
// поддомены), и строгий регекс блокировал бы рабочие случаи.
const EMAIL_RE = /^\S+@\S+\.\S+$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

// Зеркало `auth_service/src/schemas/users.py::UserCreate.username`:
//   3..128 символов, только латиница/цифры/`_`/`-`/`.`. Pattern отбивает
//   пробелы, control-chars и Unicode-homoglyphs ещё на клиенте, чтобы не
//   ловить 422 от backend.
export const USERNAME_MIN_LENGTH = 3;
export const USERNAME_MAX_LENGTH = 128;
const USERNAME_RE = /^[A-Za-z0-9_\-.]+$/;

export function validateUsername(value: string): string | null {
  const v = value.trim();
  if (v.length < USERNAME_MIN_LENGTH) {
    return `Минимум ${USERNAME_MIN_LENGTH} символа`;
  }
  if (v.length > USERNAME_MAX_LENGTH) {
    return `Максимум ${USERNAME_MAX_LENGTH} символов`;
  }
  if (!USERNAME_RE.test(v)) {
    return "Допустимы латиница, цифры и символы _ - .";
  }
  return null;
}
