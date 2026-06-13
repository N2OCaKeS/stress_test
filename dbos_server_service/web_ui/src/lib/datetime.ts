/**
 * Единый форматтер времени для UI. Backend хранит и отдаёт всё в UTC
 * (ISO-8601 с `Z`), а пользователю показываем московское время (MSK, UTC+3).
 *
 * Конвертация делается через `Intl.DateTimeFormat` с `timeZone: "Europe/Moscow"` —
 * это не зависит от часового пояса браузера и корректно для серверного
 * приложения с фиксированной зоной. Наивные обрезки строки
 * (`iso.slice(0, 16)`, `iso.replace("T", " ")`) и `toLocaleString()` без
 * `timeZone` использовать нельзя: первая показывает сырой UTC, вторая — зону
 * клиента.
 */

const DATETIME_FMT = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const DATETIME_MIN_FMT = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const DATE_FMT = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const TIME_FMT = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function toDate(value: string | number | Date): Date | null {
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function parts(fmt: Intl.DateTimeFormat, d: Date): Record<string, string> {
  return fmt.formatToParts(d).reduce<Record<string, string>>((acc, p) => {
    if (p.type !== "literal") acc[p.type] = p.value;
    return acc;
  }, {});
}

/** Placeholder для пустых/битых дат во всех рендерах. */
export const EMPTY_DT = "—";

/**
 * ISO-8601 (UTC) → `"YYYY-MM-DD HH:MM:SS MSK"`.
 * `null`/`undefined`/невалидный ввод → `"—"`.
 */
export function formatMsk(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return EMPTY_DT;
  const d = toDate(value);
  if (!d) return typeof value === "string" ? value : EMPTY_DT;
  const p = parts(DATETIME_FMT, d);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} MSK`;
}

/**
 * Компактный вариант без секунд и суффикса: `"YYYY-MM-DD HH:MM"`.
 * Подходит для плотных таблиц.
 */
export function formatMskShort(
  value: string | number | Date | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return EMPTY_DT;
  const d = toDate(value);
  if (!d) return typeof value === "string" ? value : EMPTY_DT;
  const p = parts(DATETIME_MIN_FMT, d);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/**
 * Только дата (по московскому календарю): `"YYYY-MM-DD"`.
 * Важно для полей вроде `expires_at`, где день в UTC и в MSK может различаться.
 */
export function formatMskDate(
  value: string | number | Date | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return EMPTY_DT;
  const d = toDate(value);
  if (!d) return typeof value === "string" ? value : EMPTY_DT;
  const p = parts(DATE_FMT, d);
  return `${p.year}-${p.month}-${p.day}`;
}

/**
 * Только время по московскому календарю: `"HH:MM:SS"`. Для плотных
 * списков (журнал аудита), где дата вынесена отдельно.
 */
export function formatMskTime(
  value: string | number | Date | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return EMPTY_DT;
  const d = toDate(value);
  if (!d) return typeof value === "string" ? value : EMPTY_DT;
  const p = parts(TIME_FMT, d);
  return `${p.hour}:${p.minute}:${p.second}`;
}

/**
 * Текущая дата по московскому календарю в формате `"YYYY-MM-DD"` со сдвигом
 * в днях. Используется для границ `<input type="date">` (expires-поля PAT/bot),
 * чтобы день брался по MSK, а не по UTC-полуночи браузера.
 *
 * Сначала берём сегодняшний MSK-календарный день, затем прибавляем дни уже к
 * нему — арифметику ведём через UTC-полночь этого дня, чтобы не зацепить
 * локальную зону.
 */
export function mskDateOffset(days = 0): string {
  const p = parts(DATE_FMT, new Date());
  const base = new Date(`${p.year}-${p.month}-${p.day}T00:00:00Z`);
  base.setUTCDate(base.getUTCDate() + days);
  return base.toISOString().slice(0, 10);
}

/**
 * Грубое «сколько прошло» относительно сейчас: «только что» / «N мин» /
 * «N ч» / «N дн». Для компактных колонок (последняя активность бота), где
 * точная дата не нужна. Пустой/битый ввод → «—».
 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  const min = Math.round(diff / 60_000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} ч`;
  const d = Math.round(h / 24);
  return `${d} дн`;
}
