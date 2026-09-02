import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ThemeName } from "@/types/persona";
import { emmLogoSvg } from "@/components/Logo";

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
    const root = document.documentElement;
    root.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // ignore quota / private mode
    }
    // Favicon вкладки не читает CSS-переменные (standalone-SVG), поэтому
    // перегенерируем его из токенов текущей темы при каждой смене.
    const cs = getComputedStyle(root);
    const accent = cs.getPropertyValue("--accent").trim() || "#2a8fc9";
    const ok = cs.getPropertyValue("--ok").trim() || "#4ec9b0";
    const href =
      "data:image/svg+xml," + encodeURIComponent(emmLogoSvg(accent, ok));
    let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.type = "image/svg+xml";
    link.href = href;
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
