/**
 * Обёртка над нативным `<input type="checkbox">` с опциональной подписью
 * (`.checkbox-row`, тот же паттерн, что уже использовался вручную по
 * проекту). Остаётся настоящим чекбоксом — role/keyboard-поведение нативные,
 * галочка красится глобальным `accent-color` из themes.css.
 */
import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: ReactNode;
  /** Класс на обёртку `.checkbox-row`, когда задан `label`. */
  rowClassName?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, rowClassName = "", className = "", id, ...rest },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const input = <input ref={ref} type="checkbox" id={inputId} className={className} {...rest} />;
  if (label === undefined) return input;
  return (
    <label htmlFor={inputId} className={`checkbox-row cursor-pointer ${rowClassName}`.trim()}>
      {input}
      <span>{label}</span>
    </label>
  );
});
