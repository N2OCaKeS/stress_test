/**
 * Лёгкий клиент health/ready четырёх backend-сервисов.
 *
 * Каждый сервис отдаёт `/api/<svc>/v1/health` (liveness) и
 * `/api/<svc>/v1/ready` (readiness — проверяет БД и фоновые задачи). Эти пути
 * уже проксируются Vite-дев-сервером (`vite.config.ts`), поэтому ходим прямым
 * `fetch` без авторизации — health-эндпоинты публичные и из аудита исключены.
 *
 * Статус деградирует мягко: сетевой сбой / 404 / нестандартный ответ →
 * `"down"`, прерывание по таймауту → `"unknown"`. UI рисует индикатор, но
 * никогда не падает из-за лежащего сервиса.
 */

import { API_BASE_URL } from "@/api/client";

/** Какой сервис пингуем. Совпадает с проксируемыми префиксами Vite. */
export type HealthServiceId = "auth" | "logging" | "server" | "secret";

export type HealthState = "up" | "down" | "unknown";

export interface ServiceHealth {
  id: HealthServiceId;
  /** Человекочитаемая метка для индикатора. */
  label: string;
  /** liveness (`/health`). */
  health: HealthState;
  /** readiness (`/ready`) — БД + фоновые задачи. */
  ready: HealthState;
}

interface ServiceDef {
  id: HealthServiceId;
  label: string;
  /** Префикс пути под `API_BASE_URL`, например `/auth/v1`. */
  prefix: string;
}

// loging_service проксируется под `/api/logging` (URL-префикс с двумя 'g',
// в отличие от каталога `loging`). Остальные — по имени сервиса.
const SERVICES: ServiceDef[] = [
  { id: "auth", label: "auth", prefix: "/auth/v1" },
  { id: "logging", label: "logging", prefix: "/logging/v1" },
  { id: "server", label: "server", prefix: "/server/v1" },
  { id: "secret", label: "secret", prefix: "/secret/v1" },
];

const PROBE_TIMEOUT_MS = 4000;

async function probe(path: string): Promise<HealthState> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    // /ready отдаёт 503 на неготовности, /health — 200. Любой не-2xx считаем
    // признаком проблемы (down), а не unknown: ответ получен, просто плохой.
    return res.ok ? "up" : "down";
  } catch (e) {
    // AbortError — превысили таймаут, реальное состояние неизвестно.
    if (e instanceof DOMException && e.name === "AbortError") return "unknown";
    // Сетевой сбой / refused / CORS — сервис недоступен.
    return "down";
  } finally {
    clearTimeout(timer);
  }
}

/** Пингует health + ready одного сервиса. Никогда не бросает. */
async function checkService(def: ServiceDef): Promise<ServiceHealth> {
  const [health, ready] = await Promise.all([
    probe(`${def.prefix}/health`),
    probe(`${def.prefix}/ready`),
  ]);
  return { id: def.id, label: def.label, health, ready };
}

/**
 * Пингует все четыре сервиса параллельно. Результат — массив в фиксированном
 * порядке (auth / logging / server / secret), пригодный для прямого рендера.
 */
export async function checkAllServices(): Promise<ServiceHealth[]> {
  return Promise.all(SERVICES.map(checkService));
}
