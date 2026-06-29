/**
 * Прямой пинг health-эндпоинтов backend-сервисов для страницы Cluster health.
 *
 * Отдельного `/api/cluster/*` в бэкенде нет и не планируется — вместо него
 * каждый HTTP-сервис отдаёт собственный `/health` (liveness, без БД). Ходим
 * через тот же относительный `API_BASE_URL` (по умолчанию `/api`), что и весь
 * фронт, поэтому Vite-прокси и production-ingress отдают запросы тем же
 * сервисам без CORS и без хардкода портов.
 *
 * `server_worker` HTTP-API не имеет (taskiq + Redis), его heartbeat живёт в БД
 * и из браузера недоступен — он попадает в список с флагом `probeable: false`.
 */

import { API_BASE_URL } from "@/api/client";

export interface ServicePing {
  /** Имя сервиса как в кластере (`auth_service`, …). */
  service: string;
  /** Короткая метка для индикатора. */
  label: string;
  /** Иконка строки (ключ в карте `ICONS` страницы). */
  iconName: "lock" | "server" | "key" | "doc" | "cog";
  /** Есть ли у сервиса HTTP-проба. Для `server_worker` — false. */
  probeable: boolean;
  /** Ответил ли сервис 2xx. Для непробируемых — всегда false. */
  ok: boolean;
  /** HTTP-код ответа; null при сетевом сбое, таймауте или отсутствии пробы. */
  status: number | null;
  /** Латентность запроса в мс; null если пинг не делался. */
  latency_ms: number | null;
  /** Человекочитаемая причина для не-ok результата. */
  error?: string;
}

interface HttpServiceDef {
  service: string;
  label: string;
  iconName: ServicePing["iconName"];
  /** Префикс под `API_BASE_URL`, например `/auth/v1`. */
  prefix: string;
}

// loging_service проксируется под `/api/logging` (две 'g' в URL, в отличие от
// каталога `loging`). Остальные префиксы совпадают с именем сервиса.
const HTTP_SERVICES: HttpServiceDef[] = [
  { service: "auth_service", label: "auth", iconName: "lock", prefix: "/auth/v1" },
  { service: "loging_service", label: "logging", iconName: "doc", prefix: "/logging/v1" },
  { service: "server_service", label: "server", iconName: "server", prefix: "/server/v1" },
  { service: "secret_service", label: "secret", iconName: "key", prefix: "/secret/v1" },
];

// server_worker без HTTP — добавляем отдельной строкой без пинга.
const WORKER_ENTRY: ServicePing = {
  service: "server_worker",
  label: "worker",
  iconName: "cog",
  probeable: false,
  ok: false,
  status: null,
  latency_ms: null,
  error: "нет HTTP-проба · heartbeat в БД",
};

const PROBE_TIMEOUT_MS = 5000;

function nowMs(): number {
  // performance.now() даёт монотонные миллисекунды; в jsdom/Node он тоже есть,
  // но подстрахуемся Date.now() на случай экзотического рантайма.
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

async function pingHttp(def: HttpServiceDef): Promise<ServicePing> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  const start = nowMs();
  try {
    const res = await fetch(`${API_BASE_URL}${def.prefix}/health`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal,
      credentials: "same-origin",
    });
    const latency = Math.round(nowMs() - start);
    return {
      service: def.service,
      label: def.label,
      iconName: def.iconName,
      probeable: true,
      ok: res.ok,
      status: res.status,
      latency_ms: latency,
      error: res.ok ? undefined : `HTTP ${res.status}`,
    };
  } catch (e) {
    const latency = Math.round(nowMs() - start);
    const aborted = e instanceof DOMException && e.name === "AbortError";
    return {
      service: def.service,
      label: def.label,
      iconName: def.iconName,
      probeable: true,
      ok: false,
      status: null,
      latency_ms: latency,
      error: aborted ? `таймаут ${PROBE_TIMEOUT_MS} мс` : "сеть недоступна",
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Пингует все HTTP-сервисы параллельно и возвращает строки в фиксированном
 * порядке (auth / logging / server / secret), завершая списком `server_worker`
 * как непробируемого. Никогда не бросает — каждая строка несёт свой статус.
 */
export async function pingCluster(): Promise<ServicePing[]> {
  const settled = await Promise.allSettled(HTTP_SERVICES.map(pingHttp));
  const http = settled.map((r, i) => {
    if (r.status === "fulfilled") return r.value;
    // pingHttp сам не бросает, но если будущая правка это сломает — отдаём
    // строку «down» вместо потери сервиса из списка.
    const def = HTTP_SERVICES[i];
    return {
      service: def.service,
      label: def.label,
      iconName: def.iconName,
      probeable: true,
      ok: false,
      status: null,
      latency_ms: null,
      error: "ошибка пинга",
    } satisfies ServicePing;
  });
  return [...http, WORKER_ENTRY];
}
