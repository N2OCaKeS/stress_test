import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";

const AUTH_TARGET = process.env.VITE_AUTH_PROXY_TARGET ?? "http://localhost:8000";
const LOGGING_TARGET = process.env.VITE_LOGGING_PROXY_TARGET ?? "http://localhost:8001";
const SERVER_TARGET = process.env.VITE_SERVER_PROXY_TARGET ?? "http://localhost:8002";
const SECRET_TARGET = process.env.VITE_SECRET_PROXY_TARGET ?? "http://localhost:8003";
// Агрегированный Swagger UI (BASE_URL=/docs в самом контейнере, как в проде/k8s
// — см. docker-compose.dev.yml) — тот же путь /docs, что и в проде, чтобы
// ссылки на конкретный сервис (WikiExamples.tsx) работали одинаково везде.
const SWAGGER_TARGET = process.env.VITE_SWAGGER_PROXY_TARGET ?? "http://localhost:8088";
// Grafana инфоколлектора теперь поднимается с GF_SERVER_SERVE_FROM_SUB_PATH +
// GF_SERVER_ROOT_URL=.../grafana-proxy/ (allta_infocollector/src/handler/
// docker-compose.yml) — поэтому её можно проксировать под тем же путём и
// она сама корректно резолвит статику/API. Её собственный <body> при этом
// всё равно красится в фирменный холст и игнорирует ?transparent в URL —
// это чинится тут же, CSS-инъекцией в HTML-ответ на лету.
const GRAFANA_TARGET = process.env.VITE_GRAFANA_PROXY_TARGET ?? "http://10.177.103.10:3000";
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
      "/docs": { target: SWAGGER_TARGET, changeOrigin: true },
      "/grafana-proxy": {
        target: GRAFANA_TARGET,
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
