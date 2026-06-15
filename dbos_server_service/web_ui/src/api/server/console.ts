/**
 * Хелперы консольного WebSocket (`/api/server/v1/servers/{id}/console/ws`).
 *
 * Браузерный `WebSocket` не умеет слать произвольные заголовки, поэтому
 * access-токен передаётся вторым subprotocol'ом `bearer.<token>` — бэк его
 * вынимает из заголовка Sec-WebSocket-Protocol. Сам upgrade идёт по тому же
 * origin'у, что и REST (через Vite-proxy / ingress на `/api/server`), просто
 * http(s) → ws(s).
 *
 * Source of truth (backend): server_service console WS-роут (W7-B).
 */

import { API_BASE_URL } from "@/api/client";
import { getAccessToken } from "@/api/tokenStore";

/**
 * Абсолютный ws(s)-URL консоли для сервера. `API_BASE_URL` обычно `/api`
 * (относительный), поэтому достраиваем схему и host из `window.location` —
 * соединение идёт на тот же origin, который проксирует REST.
 */
export function consoleWsUrl(serverId: string): string {
  const path = `${API_BASE_URL}/server/v1/servers/${serverId}/console/ws`;
  if (/^https?:\/\//i.test(API_BASE_URL)) {
    return path.replace(/^http/i, "ws");
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = path.startsWith("/") ? path : `/${path}`;
  return `${proto}//${window.location.host}${base}`;
}

/**
 * Subprotocol'ы для открытия консольного WS: фиксированный `console.v1` как
 * маркер протокола плюс `bearer.<access>` с текущим токеном. Если токена в
 * памяти нет — отдаём только маркер, бэк закроет соединение кодом 4401.
 */
export function consoleWsProtocols(): string[] {
  const token = getAccessToken();
  const protocols = ["console.v1"];
  if (token) protocols.push(`bearer.${token}`);
  return protocols;
}

/**
 * Человекочитаемая причина закрытия консольного WS по close-коду бэка.
 * `hint` — что предложить пользователю сделать (если применимо).
 */
export interface ConsoleCloseInfo {
  message: string;
  hint?: string;
  /** Закрытие штатное (1000) — не показываем как ошибку. */
  normal: boolean;
}

export function describeConsoleClose(
  code: number,
  reason?: string,
): ConsoleCloseInfo {
  switch (code) {
    case 1000:
      return { message: "Сессия завершена.", normal: true };
    case 4401:
      return {
        message: "Не передан токен авторизации.",
        hint: "Перезайдите в систему и попробуйте снова.",
        normal: false,
      };
    case 4403:
      return {
        message: "Нет прав на консоль этого сервера.",
        hint: "Нужна привилегия console — запросите grant у администратора сервиса или департамента.",
        normal: false,
      };
    case 4404:
      return {
        message: "Сервер не найден или принадлежит другому департаменту.",
        normal: false,
      };
    case 4409:
      return {
        message: "Сервер не готов к консольным сессиям.",
        hint: "Сначала выполните Prepare на вкладке «Управление» — без него управляющего доступа к серверу нет.",
        normal: false,
      };
    case 4503:
      return {
        message: "Сервис консолей временно недоступен.",
        hint: "Повторите попытку позже.",
        normal: false,
      };
    default: {
      const tail = reason ? ` (${reason})` : "";
      return {
        message: `Соединение закрыто (код ${code})${tail}.`,
        normal: false,
      };
    }
  }
}
