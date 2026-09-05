/**
 * Обёртка над `<span className="badge ...">` вместо ручной сборки классов
 * бейджа/статуса в каждом месте использования. Чисто визуальный элемент —
 * не влияет на доступность/семантику текста внутри.
 */
import type { HTMLAttributes } from "react";

export type BadgeKind = "neutral" | "ok" | "warn" | "danger" | "accent";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  kind?: BadgeKind;
}

const KIND_CLASS: Record<BadgeKind, string> = {
  neutral: "",
  ok: "badge-ok",
  warn: "badge-warn",
  danger: "badge-danger",
  accent: "badge-accent",
};

export function Badge({ kind = "neutral", className = "", ...rest }: BadgeProps) {
  const classes = ["badge", KIND_CLASS[kind], className].filter(Boolean).join(" ");
  return <span className={classes} {...rest} />;
}
