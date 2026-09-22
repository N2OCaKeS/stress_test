/**
 * Обёртка над нативной `<button>` с вариантами оформления вместо ручного
 * набора классов `btn btn-primary ...` в каждом месте использования.
 * Остаётся настоящим `<button>` — все нативные атрибуты (onClick, disabled,
 * title, aria-*, type) передаются как есть, роль/поведение не меняются.
 */
import { forwardRef, type ButtonHTMLAttributes } from "react";

export type ButtonVariant =
  | "default"
  | "primary"
  | "ghost"
  | "danger"
  | "danger-solid"
  | "success"
  | "success-solid";
export type ButtonSize = "default" | "sm";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  default: "",
  primary: "btn-primary",
  ghost: "btn-ghost",
  danger: "btn-danger",
  "danger-solid": "btn-danger-solid",
  success: "btn-success",
  "success-solid": "btn-success-solid",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "default", size = "default", className = "", type = "button", ...rest },
  ref,
) {
  const classes = ["btn", VARIANT_CLASS[variant], size === "sm" ? "btn-sm" : "", className]
    .filter(Boolean)
    .join(" ");
  return <button ref={ref} type={type} className={classes} {...rest} />;
});
