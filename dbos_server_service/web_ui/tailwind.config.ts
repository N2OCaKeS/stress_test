import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        "bg-soft": "var(--bg-soft)",
        "bg-soft-2": "var(--bg-soft-2)",
        border: "var(--border)",
        text: "var(--text)",
        "text-dim": "var(--text-dim)",
        accent: "var(--accent)",
        "accent-fg": "var(--accent-fg)",
        danger: "var(--danger)",
        warn: "var(--warn)",
        ok: "var(--ok)",
        hover: "var(--hover)",
        selected: "var(--selected)",
      },
      fontFamily: {
        sans: [
          "'Inter Variable'",
          "Inter",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          "'JetBrains Mono Variable'",
          "'JetBrains Mono'",
          "ui-monospace",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
