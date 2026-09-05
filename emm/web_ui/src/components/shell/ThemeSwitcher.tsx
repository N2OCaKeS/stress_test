import { useTheme, THEME_OPTIONS } from "@/contexts/ThemeContext";
import { Dropdown } from "@/components/ui/Dropdown";
import type { ThemeName } from "@/types/persona";

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  return (
    <Dropdown
      mode="single"
      options={THEME_OPTIONS}
      value={theme}
      onChange={(v) => setTheme(v as ThemeName)}
    />
  );
}
