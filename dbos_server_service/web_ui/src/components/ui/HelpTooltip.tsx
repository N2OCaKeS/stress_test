/**
 * Маленькая иконка-вопрос рядом с подписью поля. По клику разворачивает
 * поповер с короткой справкой. Закрывается по клику вне и по Esc.
 *
 * Позиционируется абсолютно от своего якоря, layout формы не двигает.
 *
 * `inline` рендерит триггер как `<span role="button">` вместо `<button>` —
 * нужно, когда тултип живёт внутри другого кликабельного элемента (строка-
 * кнопка списка): вложенная кнопка — невалидный HTML. В обоих режимах клик по
 * триггеру не всплывает к родителю.
 */
import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import { HelpCircle } from "lucide-react";

export function HelpTooltip({
  text,
  label,
  inline = false,
}: {
  text: string;
  label?: string;
  inline?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popId = useId();

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle(e: ReactMouseEvent | ReactKeyboardEvent) {
    e.stopPropagation();
    setOpen((v) => !v);
  }

  const triggerProps = {
    "aria-label": label ?? "Справка по полю",
    "aria-expanded": open,
    "aria-controls": open ? popId : undefined,
    className: "text-dim hover:text-accent inline-flex",
    onClick: toggle,
    onMouseEnter: () => setOpen(true),
    onMouseLeave: () => setOpen(false),
  };

  return (
    <span ref={wrapRef} className="relative inline-flex align-middle">
      {inline ? (
        <span
          role="button"
          tabIndex={0}
          {...triggerProps}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              toggle(e);
            }
          }}
        >
          <HelpCircle className="w-3.5 h-3.5" />
        </span>
      ) : (
        <button type="button" {...triggerProps}>
          <HelpCircle className="w-3.5 h-3.5" />
        </button>
      )}
      {open && (
        <span
          id={popId}
          role="tooltip"
          className="absolute z-50 left-0 top-5 w-56 surface border border-token rounded shadow-lg px-2.5 py-2 text-xs text-text font-normal normal-case leading-snug"
        >
          {text}
        </span>
      )}
    </span>
  );
}
