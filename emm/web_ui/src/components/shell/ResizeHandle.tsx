import { useEffect, useRef, useState } from "react";

interface ResizeHandleProps {
  width: number;
  onChange: (next: number) => void;
  min: number;
  max: number;
  resetTo?: number;
  ariaLabel?: string;
}

/**
 * Vertical draggable separator. 8px hit area, 1px visible line, highlighted
 * on hover/drag.
 */
export function ResizeHandle({
  width,
  onChange,
  min,
  max,
  resetTo,
  ariaLabel = "Resize",
}: ResizeHandleProps) {
  const stateRef = useRef({ startX: 0, startWidth: width, dragging: false });
  const onChangeRef = useRef(onChange);
  const minMaxRef = useRef({ min, max });
  const [active, setActive] = useState(false);

  onChangeRef.current = onChange;
  minMaxRef.current = { min, max };

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!stateRef.current.dragging) return;
      const delta = e.clientX - stateRef.current.startX;
      const next = stateRef.current.startWidth + delta;
      const { min: mn, max: mx } = minMaxRef.current;
      onChangeRef.current(Math.min(mx, Math.max(mn, next)));
    }
    function onUp() {
      if (!stateRef.current.dragging) return;
      stateRef.current.dragging = false;
      setActive(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    stateRef.current = { startX: e.clientX, startWidth: width, dragging: true };
    setActive(true);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const onDoubleClick = () => {
    if (resetTo != null) onChangeRef.current(resetTo);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      onMouseDown={onMouseDown}
      onDoubleClick={onDoubleClick}
      className="group relative w-2 shrink-0 cursor-col-resize select-none"
      style={{ touchAction: "none" }}
    >
      <div
        className={`absolute inset-y-0 left-1/2 -translate-x-1/2 transition-colors ${
          active
            ? "bg-accent w-[2px]"
            : "bg-[var(--border)] w-px group-hover:bg-accent group-hover:w-[2px]"
        }`}
      />
    </div>
  );
}

interface HeightResizeHandleProps {
  onChange: (next: number) => void;
  onReset?: () => void;
  measure: () => number;
  min: number;
  max: number;
  ariaLabel?: string;
}

/**
 * Горизонтальный разделитель для регулировки высоты (тянем ↕). Стартовую высоту
 * берём из `measure()` в момент нажатия — так работает и когда панель ещё
 * заполняет доступное место без явной высоты. Двойной клик сбрасывает оверрайд.
 */
export function HeightResizeHandle({
  onChange,
  onReset,
  measure,
  min,
  max,
  ariaLabel = "Resize height",
}: HeightResizeHandleProps) {
  const stateRef = useRef({ startY: 0, startHeight: 0, dragging: false });
  const onChangeRef = useRef(onChange);
  const measureRef = useRef(measure);
  const minMaxRef = useRef({ min, max });
  const [active, setActive] = useState(false);

  onChangeRef.current = onChange;
  measureRef.current = measure;
  minMaxRef.current = { min, max };

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!stateRef.current.dragging) return;
      const delta = e.clientY - stateRef.current.startY;
      const next = stateRef.current.startHeight + delta;
      const { min: mn, max: mx } = minMaxRef.current;
      onChangeRef.current(Math.min(mx, Math.max(mn, next)));
    }
    function onUp() {
      if (!stateRef.current.dragging) return;
      stateRef.current.dragging = false;
      setActive(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    stateRef.current = {
      startY: e.clientY,
      startHeight: measureRef.current(),
      dragging: true,
    };
    setActive(true);
    document.body.style.cursor = "ns-resize";
    document.body.style.userSelect = "none";
  };

  const onDoubleClick = () => {
    onReset?.();
  };

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={ariaLabel}
      onMouseDown={onMouseDown}
      onDoubleClick={onDoubleClick}
      className="group relative h-2 shrink-0 cursor-ns-resize select-none"
      style={{ touchAction: "none" }}
    >
      <div
        className={`absolute inset-x-0 top-1/2 -translate-y-1/2 transition-colors ${
          active
            ? "bg-accent h-[2px]"
            : "bg-[var(--border)] h-px group-hover:bg-accent group-hover:h-[2px]"
        }`}
      />
    </div>
  );
}
