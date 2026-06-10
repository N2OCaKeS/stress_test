import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const AUTH_TARGET = process.env.VITE_AUTH_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: {
      "/api/auth": {
        target: AUTH_TARGET,
        changeOrigin: true,
      },
    },
  },
});
