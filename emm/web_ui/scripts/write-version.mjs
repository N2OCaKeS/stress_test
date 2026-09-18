// Пишет public/version.json перед сборкой (npm запускает этот скрипт как
// "prebuild" перед "build" автоматически). Файл попадает в dist/ вместе с
// остальной публичной статикой и раздаётся тем же nginx, что и index.html —
// никакого отдельного бэкенд-эндпоинта не нужно.
//
// Формат: { "sha": "<короткий git SHA>", "builtAt": "<ISO-время сборки>" }.
// Фронт сравнивает "sha" с тем, что было при открытии вкладки, и показывает
// баннер обновления, если билд на сервере уже другой (см. src/lib/appVersion.ts).
//
// SHA сначала берём из окружения (GIT_SHA / CI-переменные) — это нужно для
// сборки в Docker, где .git не входит в build context (см. .dockerignore) и
// git-команда внутри контейнера ничего не найдёт. Локальный "npm run build"
// вне Docker подхватывает git сам, через обычный rev-parse.
import { execSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

export function resolveBuildSha(env = process.env, exec = execSync) {
  const fromEnv = env.GIT_SHA || env.CI_COMMIT_SHA || env.SOURCE_VERSION;
  if (fromEnv && fromEnv.trim()) return fromEnv.trim().slice(0, 12);
  try {
    return exec("git rev-parse --short HEAD", {
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim();
  } catch {
    return "unknown";
  }
}

export function buildVersionPayload(env = process.env, exec = execSync) {
  return {
    sha: resolveBuildSha(env, exec),
    builtAt: new Date().toISOString(),
  };
}

function main() {
  const outPath = fileURLToPath(new URL("../public/version.json", import.meta.url));
  writeFileSync(outPath, JSON.stringify(buildVersionPayload(), null, 2) + "\n");
  console.log(`version.json written: ${path.relative(process.cwd(), outPath)}`);
}

// Запускать main() только при прямом вызове (node scripts/write-version.mjs),
// не при импорте из тестов.
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
