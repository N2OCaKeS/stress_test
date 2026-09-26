/**
 * Поле шаблона с подстановками `{CODE}`: переменная-шаблон
 * (`source=template`) и `override_value` слота команды.
 *
 * Подсветка — строка под полем, где каждая `{CODE}` показана отдельно:
 * известная переменная — акцентом, неизвестная — красным (сервис такой
 * шаблон не сохранит: `VARIABLE_TEMPLATE_UNKNOWN`). Автодополнение — после
 * `{` список кодов каталога, отфильтрованный по набранному; выбор — клик,
 * Enter или Tab, стрелки двигают выделение, Escape закрывает.
 *
 * Формат кода — как у сервиса (`variableSourceRef.PLACEHOLDER_RE`).
 */
import { useMemo, useRef, useState } from "react";
import { PLACEHOLDER_RE } from "./variableSourceRef";

// Незакрытая подстановка перед кареткой: `{`, `{RC`, `{rc_n`.
const OPEN_PLACEHOLDER_RE = /\{([A-Za-z0-9_]*)$/;
const MAX_SUGGESTIONS = 8;

type Segment = { text: string; code?: string };

function segments(text: string): Segment[] {
  const out: Segment[] = [];
  let pos = 0;
  for (const match of text.matchAll(PLACEHOLDER_RE)) {
    const start = match.index ?? 0;
    if (start > pos) out.push({ text: text.slice(pos, start) });
    out.push({ text: match[0], code: match[1] });
    pos = start + match[0].length;
  }
  if (pos < text.length) out.push({ text: text.slice(pos) });
  return out;
}

export function TemplateInput({
  value,
  onChange,
  codes,
  placeholder,
  ariaLabel,
  invalid = false,
}: {
  value: string;
  onChange: (value: string) => void;
  /** Коды каталога переменных — для подсветки и автодополнения. */
  codes: readonly string[];
  placeholder?: string;
  ariaLabel?: string;
  /** Подсветить рамку как ошибочную (ошибка сервиса у поля). */
  invalid?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [caret, setCaret] = useState<number | null>(null);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);

  const known = useMemo(() => new Set(codes), [codes]);
  const open = caret === null ? null : OPEN_PLACEHOLDER_RE.exec(value.slice(0, caret));
  const suggestions = useMemo(() => {
    if (!open) return [];
    const prefix = open[1].toUpperCase();
    return [...codes].filter((code) => code.startsWith(prefix)).sort().slice(0, MAX_SUGGESTIONS);
  }, [open, codes]);
  const showSuggestions = !dismissed && suggestions.length > 0;
  const parts = useMemo(() => segments(value), [value]);
  const hasCodes = parts.some((part) => part.code);

  function track(target: HTMLInputElement) {
    setCaret(target.selectionStart);
  }

  function accept(code: string) {
    if (!open || caret === null) return;
    const start = caret - open[0].length;
    const next = `${value.slice(0, start)}{${code}}${value.slice(caret)}`;
    const nextCaret = start + code.length + 2;
    onChange(next);
    setCaret(nextCaret);
    setActive(0);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(nextCaret, nextCaret);
    });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!showSuggestions) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      accept(suggestions[Math.min(active, suggestions.length - 1)]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setDismissed(true);
    }
  }

  return (
    <div className="relative flex flex-col gap-1">
      <input
        ref={inputRef}
        className={`surface-2 border rounded px-2 py-1 mono text-sm ${invalid ? "border-danger" : "border-token"}`}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-invalid={invalid || undefined}
        aria-autocomplete="list"
        aria-expanded={showSuggestions}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setDismissed(false);
          setActive(0);
          track(e.target);
        }}
        onSelect={(e) => track(e.currentTarget)}
        onClick={(e) => track(e.currentTarget)}
        onKeyDown={onKeyDown}
        onBlur={() => setCaret(null)}
      />
      {showSuggestions && (
        <div
          role="listbox"
          aria-label="Переменные для подстановки"
          className="absolute left-0 right-0 top-full z-10 mt-1 surface border border-token rounded shadow-lg max-h-56 overflow-auto"
        >
          {suggestions.map((code, index) => (
            <button
              key={code}
              type="button"
              role="option"
              aria-selected={index === active}
              className={`block w-full text-left px-2 py-1 mono text-xs ${index === active ? "surface-2 text-accent" : ""}`}
              // mousedown, а не click: иначе поле теряет фокус (и каретку) раньше выбора.
              onMouseDown={(e) => {
                e.preventDefault();
                accept(code);
              }}
            >
              {`{${code}}`}
            </button>
          ))}
        </div>
      )}
      {hasCodes && (
        <div className="mono text-[11px] leading-5 break-all" data-testid="template-highlight">
          {parts.map((part, index) =>
            part.code ? (
              <span
                key={index}
                className={`rounded px-0.5 ${known.has(part.code) ? "text-accent surface-2" : "text-danger surface-2 underline"}`}
                title={known.has(part.code) ? undefined : "Неизвестная переменная"}
              >
                {part.text}
              </span>
            ) : (
              <span key={index} className="text-dim">{part.text}</span>
            ),
          )}
        </div>
      )}
    </div>
  );
}
