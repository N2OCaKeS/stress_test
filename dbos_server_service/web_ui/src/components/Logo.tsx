import { useId } from "react";

interface EmmLogoProps {
  className?: string;
  title?: string;
}

/**
 * Логотип EMM — скруглённая плитка с градиентом и монограммой «m».
 * Самодостаточный SVG: одинаково читается на светлой и тёмной теме,
 * не зависит от текущих цветов и остаётся чётким на мелком размере (favicon).
 */
export function EmmLogo({ className, title = "EMM" }: EmmLogoProps) {
  const gid = useId();
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      role="img"
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient
          id={gid}
          x1="0"
          y1="0"
          x2="32"
          y2="32"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#1291bb" />
          <stop offset="1" stopColor="#4ec9b0" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="7.5" fill={`url(#${gid})`} />
      <path
        d="M9.5 21.5 V15.2 a3.1 3.1 0 0 1 6.2 0 V21.5 M15.7 15.2 a3.1 3.1 0 0 1 6.2 0 V21.5"
        fill="none"
        stroke="#ffffff"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
