import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const AUTH_TARGET = process.env.VITE_AUTH_PROXY_TARGET ?? "http://localhost:8000";
const LOGGING_TARGET = process.env.VITE_LOGGING_PROXY_TARGET ?? "http://localhost:8001";
const SERVER_TARGET = process.env.VITE_SERVER_PROXY_TARGET ?? "http://localhost:8002";
const SECRET_TARGET = process.env.VITE_SECRET_PROXY_TARGET ?? "http://localhost:8003";

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
      "/api/auth": { target: AUTH_TARGET, changeOrigin: true },
      "/api/logging": { target: LOGGING_TARGET, changeOrigin: true },
      "/api/loging": { target: LOGGING_TARGET, changeOrigin: true },
      "/api/server": { target: SERVER_TARGET, changeOrigin: true, ws: true },
      "/api/secret": { target: SECRET_TARGET, changeOrigin: true },
    },
  },
});
