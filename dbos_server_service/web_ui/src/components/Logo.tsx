interface EmmLogoProps {
  className?: string;
  title?: string;
}

/**
 * Логотип EMM — кластер: центральный управляющий узел и три машины со связями.
 * Цвета берутся из токенов темы (`--accent` для узла/связей, `--ok` для машин),
 * поэтому логотип в шапке меняется вместе с темой. Тот же рисунок строкой —
 * `emmLogoSvg` (для favicon-data-URI, где CSS-переменные недоступны).
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
      <g
        stroke="var(--accent)"
        strokeWidth="1.8"
        strokeLinecap="round"
        opacity="0.55"
      >
        <line x1="16" y1="16" x2="16" y2="6.5" />
        <line x1="16" y1="16" x2="7" y2="24" />
        <line x1="16" y1="16" x2="25" y2="24" />
      </g>
      <g fill="var(--ok)">
        <circle cx="16" cy="6.5" r="3" />
        <circle cx="7" cy="24" r="3" />
        <circle cx="25" cy="24" r="3" />
      </g>
      <rect x="11" y="11" width="10" height="10" rx="3" fill="var(--accent)" />
    </svg>
  );
}

/**
 * Тот же логотип строкой с конкретными цветами — для favicon (`data:` URI):
 * standalone-SVG во вкладке не видит CSS-переменные приложения, поэтому цвета
 * подставляются из вычисленных токенов текущей темы (см. ThemeProvider).
 */
export function emmLogoSvg(accent: string, ok: string): string {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">` +
    `<g stroke="${accent}" stroke-width="1.8" stroke-linecap="round" opacity="0.55">` +
    `<line x1="16" y1="16" x2="16" y2="6.5"/>` +
    `<line x1="16" y1="16" x2="7" y2="24"/>` +
    `<line x1="16" y1="16" x2="25" y2="24"/></g>` +
    `<g fill="${ok}">` +
    `<circle cx="16" cy="6.5" r="3"/>` +
    `<circle cx="7" cy="24" r="3"/>` +
    `<circle cx="25" cy="24" r="3"/></g>` +
    `<rect x="11" y="11" width="10" height="10" rx="3" fill="${accent}"/></svg>`
  );
}
