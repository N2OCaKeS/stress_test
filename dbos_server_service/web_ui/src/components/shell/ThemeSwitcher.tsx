import { useTheme, THEME_OPTIONS } from "@/contexts/ThemeContext";
import type { ThemeName } from "@/types/persona";

/**
 * Native <select> for theme. Radix Select is available for richer pickers
 * but the top-bar lives in a 32px slot; a native select fits that slot and
 * adds no popover surface.
 */
export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  return (
    <select
      value={theme}
      onChange={(e) => setTheme(e.target.value as ThemeName)}
      className="surface-2 border border-token rounded px-2 py-1 text-sm w-full"
      aria-label="Theme"
    >
      {THEME_OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
