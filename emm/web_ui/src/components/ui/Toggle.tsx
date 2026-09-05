/**
 * Переключатель-слайдер (в отличие от `Checkbox` — визуально свитч, а не
 * галочка), поверх реального `<input type="checkbox" role="switch">`, чтобы
 * клавиатура и скринридер видели обычный булев контрол, а не кастомную
 * кнопку. Раньше в проекте свитч эмулировался кнопкой `btn btn-primary`
 * (см. `pages/server/tabs/power.tsx`) — этот компонент даёт единый вид
 * вместо перепридумывания в каждом месте.
 */
import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";

export interface ToggleProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "size"> {
  label?: ReactNode;
  rowClassName?: string;
}

export const Toggle = forwardRef<HTMLInputElement, ToggleProps>(function Toggle(
  { label, rowClassName = "", className = "", id, checked, ...rest },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const track = (
    <span className={`toggle-track ${checked ? "toggle-track-on" : ""} ${className}`.trim()}>
      <input ref={ref} type="checkbox" role="switch" id={inputId} checked={checked} className="toggle-input" {...rest} />
      <span className="toggle-thumb" aria-hidden="true" />
    </span>
  );
  if (label === undefined) return track;
  return (
    <label htmlFor={inputId} className={`flex items-center gap-2 text-sm cursor-pointer select-none ${rowClassName}`.trim()}>
      {track}
      <span>{label}</span>
    </label>
  );
});
