/**
 * Опрос `/version.json` для баннера «доступна новая версия интерфейса».
 *
 * Файл пишется build-скриптом (`scripts/write-version.mjs`, npm-хук
 * "prebuild") и раздаётся как обычная статика рядом с `index.html` — того же
 * SPA-nginx достаточно, отдельный бэкенд-эндпоинт не нужен.
 *
 * В dev-режиме (`vite dev` без прогона build-скрипта) файла на диске нет —
 * запрос вернёт 404, и это трактуется как «версию узнать не удалось», без
 * ошибок в консоли и без баннера.
 */

export const VERSION_URL = "/version.json";
export const VERSION_POLL_INTERVAL_MS = 5 * 60 * 1000;

interface VersionPayload {
  sha?: unknown;
}

/** Текущий SHA сборки с сервера, либо null если файла нет / ответ невалиден. */
export async function fetchBuildSha(): Promise<string | null> {
  try {
    const res = await fetch(`${VERSION_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as VersionPayload;
    return typeof data.sha === "string" && data.sha.length > 0 ? data.sha : null;
  } catch {
    return null;
  }
}
