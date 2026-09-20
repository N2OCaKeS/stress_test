/**
 * Проверка HTML, который отдаёт Shiki, перед вставкой через
 * `dangerouslySetInnerHTML`. Shiki сам экранирует текст токенов, а это вторая,
 * независимая линия защиты: в примерах кода оказываются значения, которые
 * пользователь вводит на странице (Base URL, токен, PAT).
 *
 * Легитимный вывод Shiki — это только `<pre><code><span>…</span></code></pre>`
 * с атрибутами `class`, `style`, `tabindex`. Любое отклонение (другой тег,
 * лишний атрибут, `on*`, `url(...)` в стиле, комментарий) — повод не вставлять
 * разметку вовсе: возвращаем `null`, а компонент показывает обычный `<pre>` с
 * текстом, который React экранирует сам.
 *
 * Разбор идёт через `DOMParser` — документ неактивный, скрипты и загрузка
 * картинок в нём не срабатывают. Результат — повторная сериализация уже
 * проверенного дерева, а не исходная строка.
 */

const ALLOWED_TAGS = new Set(["PRE", "CODE", "SPAN"]);
const ALLOWED_ATTRS = new Set(["class", "style", "tabindex"]);
const FORBIDDEN_STYLE = /url\s*\(|expression|javascript:|@import|[<>\\]/i;

function isSafeNode(node: Node): boolean {
  if (node.nodeType === Node.TEXT_NODE) return true;
  if (node.nodeType !== Node.ELEMENT_NODE) return false;

  const el = node as Element;
  if (!ALLOWED_TAGS.has(el.tagName)) return false;
  for (const attr of Array.from(el.attributes)) {
    if (!ALLOWED_ATTRS.has(attr.name)) return false;
    if (attr.name === "style" && FORBIDDEN_STYLE.test(attr.value)) return false;
  }
  return Array.from(el.childNodes).every(isSafeNode);
}

export function sanitizeShikiHtml(html: string): string | null {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const nodes = Array.from(doc.body.childNodes);
  if (nodes.length === 0 || !nodes.every(isSafeNode)) return null;
  return doc.body.innerHTML;
}
