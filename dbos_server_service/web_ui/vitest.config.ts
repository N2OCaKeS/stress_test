import { defineConfig } from "vitest/config";
import path from "node:path";

// React plugin is intentionally omitted here: Vitest 2 bundles its own Vite
// and the @vitejs/plugin-react types alias to the user's Vite; cross-instance
// Plugin types do not unify. JSX transform via esbuild is enough for tests
// since we do not exercise React Fast Refresh in jsdom runs.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  esbuild: {
    jsx: "automatic",
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
    testTimeout: 15_000,
  },
});
