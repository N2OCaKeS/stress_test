import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ThemeName } from "@/types/persona";

const STORAGE_KEY = "dbos-theme";
const DEFAULT_THEME: ThemeName = "vscode-dark";

export const THEME_OPTIONS: { value: ThemeName; label: string }[] = [
  { value: "vscode-dark", label: "VS Code Dark" },
  { value: "vscode-light", label: "VS Code Light" },
  { value: "dark-orange", label: "Dark Orange" },
  { value: "blue", label: "Blue" },
];

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

function readStored(): ThemeName {
  if (typeof window === "undefined") return DEFAULT_THEME;
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored && THEME_OPTIONS.some((o) => o.value === stored)) {
    return stored as ThemeName;
  }
  return DEFAULT_THEME;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>(readStored);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // ignore quota / private mode
    }
  }, [theme]);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme: setThemeState }),
    [theme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
