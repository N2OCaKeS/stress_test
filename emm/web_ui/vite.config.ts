import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";

const AUTH_TARGET = process.env.VITE_AUTH_PROXY_TARGET ?? "http://localhost:8000";
const LOGGING_TARGET = process.env.VITE_LOGGING_PROXY_TARGET ?? "http://localhost:8001";
const SERVER_TARGET = process.env.VITE_SERVER_PROXY_TARGET ?? "http://localhost:8002";
const SECRET_TARGET = process.env.VITE_SECRET_PROXY_TARGET ?? "http://localhost:8003";
const GRAFANA_TRANSPARENT_STYLE =
  "<style>html,body,.app-grafana{background:transparent!important}</style>";

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
      "/grafana-proxy": {
        target: "http://local_infocollector-grafana-1:3000",
        changeOrigin: true,
        ws: true,
        selfHandleResponse: true,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes, _req: IncomingMessage, res: ServerResponse) => {
            const chunks: Buffer[] = [];
            proxyRes.on("data", (chunk: Buffer) => chunks.push(chunk));
            proxyRes.on("end", () => {
              const contentType = String(proxyRes.headers["content-type"] ?? "");
              const headers = { ...proxyRes.headers };
              delete headers["content-length"];
              let body: Buffer<ArrayBufferLike> = Buffer.concat(chunks);
              if (contentType.includes("text/html") && body.includes("</head>")) {
                body = Buffer.from(
                  body.toString("utf-8").replace("</head>", `${GRAFANA_TRANSPARENT_STYLE}</head>`),
                  "utf-8",
                );
              }
              res.writeHead(proxyRes.statusCode ?? 200, headers);
              res.end(body);
            });
          });
        },
      },
    },
  },
});
