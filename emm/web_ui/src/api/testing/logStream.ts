/**
 * Хелперы WS живого лога теста
 * (`/api/testing/v1/queue-items/{id}/log/stream`), по образцу
 * `@/api/server/console` (`consoleWsUrl`/`consoleWsProtocols`) — тот же приём
 * передачи access-токена через subprotocol, т.к. браузерный `WebSocket` не
 * умеет слать произвольные заголовки.
 *
 * В отличие от интерактивной SSH-консоли, этот канал read-only: backend не
 * ждёт входящих кадров, только поллит и шлёт дельту текста лога (§8.6 плана
 * миграции). Source of truth (backend) —
 * `testing_service/src/api/v1/endpoints/test_logs.py::stream_log`.
 */

import { API_BASE_URL } from "@/api/client";
import { getAccessToken } from "@/api/tokenStore";

/** Маркер-subprotocol — должен совпадать с `_LOG_STREAM_SUBPROTOCOL` бэка. */
const PROTOCOL_MARKER = "testing-log.v1";

/** Абсолютный ws(s)-URL живого лога для одного элемента очереди. */
export function testLogStreamUrl(queueItemId: string): string {
  const path = `${API_BASE_URL}/testing/v1/queue-items/${queueItemId}/log/stream`;
  if (/^https?:\/\//i.test(API_BASE_URL)) {
    return path.replace(/^http/i, "ws");
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = path.startsWith("/") ? path : `/${path}`;
  return `${proto}//${window.location.host}${base}`;
}

/** Subprotocol'ы для открытия WS: маркер + `bearer.<access>` с текущим токеном. */
export function testLogStreamProtocols(): string[] {
  const token = getAccessToken();
  const protocols = [PROTOCOL_MARKER];
  if (token) protocols.push(`bearer.${token}`);
  return protocols;
}
