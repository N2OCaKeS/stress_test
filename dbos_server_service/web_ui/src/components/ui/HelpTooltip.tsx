/**
 * Маленькая иконка-вопрос рядом с подписью поля. По клику разворачивает
 * поповер с короткой справкой. Закрывается по клику вне и по Esc.
 *
 * Позиционируется абсолютно от своего якоря, layout формы не двигает.
 */
import { useEffect, useId, useRef, useState } from "react";
import { HelpCircle } from "lucide-react";

export function HelpTooltip({ text, label }: { text: string; label?: string }) {
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

  return (
    <span ref={wrapRef} className="relative inline-flex align-middle">
      <button
        type="button"
        aria-label={label ?? "Справка по полю"}
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        className="text-dim hover:text-accent inline-flex"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        <HelpCircle className="w-3.5 h-3.5" />
      </button>
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
