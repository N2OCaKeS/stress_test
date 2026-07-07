interface EmmLogoProps {
  className?: string;
  title?: string;
}

/**
 * Логотип EMM — монограмма «m» как сетевой узел: две открытые дуги на трёх
 * стойках-узлах (управляющий узел + машины). Цвета берутся из токенов темы
 * (`--accent` для дуг, `--ok` для узлов), поэтому логотип меняется вместе со
 * светлой/тёмной темой. Для favicon см. `public/favicon.svg` (фикс-цвета —
 * standalone-SVG не видит CSS-переменные приложения).
 */
export function EmmLogo({ className, title = "EMM" }: EmmLogoProps) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      role="img"
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M8 21 V13 a4 4 0 0 1 8 0 V21 M16 13 a4 4 0 0 1 8 0 V21"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <g fill="var(--ok)">
        <circle cx="8" cy="21" r="2.7" />
        <circle cx="16" cy="21" r="2.7" />
        <circle cx="24" cy="21" r="2.7" />
      </g>
    </svg>
  );
}
