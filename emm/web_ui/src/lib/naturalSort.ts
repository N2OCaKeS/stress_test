/**
 * «Натуральное» сравнение строк с числами: `"179"` < `"1710rc45"` < `"1711rc17"`,
 * `"stand3"` < `"stand11"`, `1.7.9` < `1.7.10` — числовые куски сравниваются
 * как числа, а не посимвольно. Без этого `localeCompare`/дефолтный `.sort()`
 * даёт лексикографический порядок (`"10" < "11" < ... < "3" < "4"`), что и
 * ломает список стендов по номеру и версий ОС по имени.
 *
 * Обёртка над `String.localeCompare(..., { numeric: true })` — встроенная
 * Intl-коллация уже умеет это делать, свой парсер не нужен.
 */
export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}
