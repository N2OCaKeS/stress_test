/**
 * Маленькая иконка-вопрос рядом с подписью поля. По клику разворачивает
 * поповер с короткой справкой. Закрывается по клику вне и по Esc.
 *
 * Поповер рендерится порталом в `document.body` и позиционируется по
 * координатам якоря, поэтому не обрезается контейнерами с overflow (скролл
 * рабочих зон) и ложится поверх остального. Layout формы не двигает.
 *
 * `inline` рендерит триггер как `<span role="button">` вместо `<button>` —
 * нужно, когда тултип живёт внутри другого кликабельного элемента (строка-
 * кнопка списка): вложенная кнопка — невалидный HTML. В обоих режимах клик по
 * триггеру не всплывает к родителю.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import { HelpCircle } from "lucide-react";

type Pos = { top: number; left: number };

const POPOVER_WIDTH = 224; // соответствует w-56
const GAP = 4; // зазор между иконкой и поповером

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
  const [pos, setPos] = useState<Pos | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const popRef = useRef<HTMLSpanElement>(null);
  const popId = useId();

  const place = useCallback(() => {
    const trigger = wrapRef.current;
    if (!trigger) return;
    const r = trigger.getBoundingClientRect();
    // Прижимаем к левому краю якоря, но не даём вылезти за правый край окна.
    const maxLeft = Math.max(GAP, window.innerWidth - POPOVER_WIDTH - GAP);
    setPos({
      top: r.bottom + GAP,
      left: Math.min(r.left, maxLeft),
    });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      const insideTrigger = wrapRef.current?.contains(t);
      const insidePopover = popRef.current?.contains(t);
      if (!insideTrigger && !insidePopover) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

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
      {open &&
        pos &&
        createPortal(
          <span
            ref={popRef}
            id={popId}
            role="tooltip"
            style={{ position: "fixed", top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
            className="z-[1000] surface border border-token rounded shadow-lg px-2.5 py-2 text-xs text-text font-normal normal-case leading-snug"
            onMouseEnter={() => setOpen(true)}
            onMouseLeave={() => setOpen(false)}
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  );
}
