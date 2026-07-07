interface EmmLogoProps {
  className?: string;
  title?: string;
}

/**
 * Логотип EMM — окно терминала с приглашением `>_`. Цвета берутся из токенов
 * темы (`--accent` для окна, `--ok` для промпта и точек), поэтому логотип в
 * шапке меняется вместе с темой. Тот же рисунок строкой — `emmLogoSvg` (для
 * favicon-data-URI, где CSS-переменные недоступны).
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
      <rect
        x="4"
        y="7"
        width="24"
        height="18"
        rx="3"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2.2"
      />
      <line x1="5" y1="12.5" x2="27" y2="12.5" stroke="var(--accent)" strokeWidth="2" />
      <g fill="var(--ok)">
        <circle cx="7.6" cy="9.8" r="0.95" />
        <circle cx="10.4" cy="9.8" r="0.95" />
      </g>
      <path
        d="M9 16.5 l3 2.4 l-3 2.4"
        fill="none"
        stroke="var(--ok)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line x1="15" y1="21.3" x2="20" y2="21.3" stroke="var(--ok)" strokeWidth="2" strokeLinecap="round" />
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
    `<rect x="4" y="7" width="24" height="18" rx="3" fill="none" stroke="${accent}" stroke-width="2.2"/>` +
    `<line x1="5" y1="12.5" x2="27" y2="12.5" stroke="${accent}" stroke-width="2"/>` +
    `<g fill="${ok}"><circle cx="7.6" cy="9.8" r="0.95"/><circle cx="10.4" cy="9.8" r="0.95"/></g>` +
    `<path d="M9 16.5 l3 2.4 l-3 2.4" fill="none" stroke="${ok}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<line x1="15" y1="21.3" x2="20" y2="21.3" stroke="${ok}" stroke-width="2" stroke-linecap="round"/></svg>`
  );
}
