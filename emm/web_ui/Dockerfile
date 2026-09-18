FROM node:22-alpine AS build

WORKDIR /app

# Сначала манифесты — лучше кэшируется слой с npm ci.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# .git не входит в build context (см. .dockerignore), поэтому prebuild-хук
# (scripts/write-version.mjs) не может сам взять SHA через git rev-parse
# внутри контейнера — прокидываем его снаружи build-arg'ом. Не передали —
# version.json уйдёт с sha: "unknown" (баннер обновления просто не сработает).
ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA
RUN npm run build

FROM nginx:1.27-alpine AS serve

# Статика собранного SPA + server-блок с SPA-fallback и security-заголовками.
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
