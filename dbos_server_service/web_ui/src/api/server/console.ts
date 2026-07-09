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
 *
 * `accountId` — сервисная учётка, под которой бэк откроет PTY. Уходит в
 * query `account_id`; без него бэк закроет соединение кодом 4400.
 */
export function consoleWsUrl(serverId: string, accountId: string): string {
  const path = `${API_BASE_URL}/server/v1/servers/${serverId}/console/ws?account_id=${encodeURIComponent(accountId)}`;
  if (/^https?:\/\//i.test(API_BASE_URL)) {
    return path.replace(/^http/i, "ws");
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = path.startsWith("/") ? path : `/${path}`;
  return `${proto}//${window.location.host}${base}`;
}

/**
 * Абсолютный ws(s)-URL консоли для ВМ — тот же приём, что и `consoleWsUrl` для
 * сервера, только путь ведёт в `/vms/{id}/console/ws`. `accountId` уходит в
 * query `account_id`: бэк открывает сессию в гость под этой учёткой. `kind`
 * выбирает транспорт: `ssh` (PTY в гость) или `serial` (`virsh console`).
 */
export function vmConsoleWsUrl(
  vmId: string,
  accountId: string,
  kind: "ssh" | "serial" = "ssh",
): string {
  const path = `${API_BASE_URL}/server/v1/vms/${vmId}/console/ws?account_id=${encodeURIComponent(accountId)}&kind=${kind}`;
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
    case 4400:
      return {
        message: "Не выбран аккаунт для подключения.",
        hint: "Выберите сервисную учётку и подключитесь снова.",
        normal: false,
      };
    case 4401:
      return {
        message: "Нет авторизации — токен не передан.",
        hint: "Перезайдите в систему и попробуйте снова.",
        normal: false,
      };
    case 4403:
      return {
        message: "Нет прав на консоль или на выбранный аккаунт.",
        hint: "Нужен доступ к учётке с правом console или view_password — запросите grant у администратора сервиса или департамента.",
        normal: false,
      };
    case 4404:
      return {
        message: "Сервер или аккаунт не найден.",
        hint: "Возможно, ресурс удалён или принадлежит другому департаменту.",
        normal: false,
      };
    case 4409:
      // Конфликт состояния сервера. Различаем по reason'у бэка: занят
      // другим пользователем, выведен из эксплуатации, либо у учётки нет
      // сохранённого пароля.
      if (reason === "SERVER_BUSY") {
        return {
          message:
            "Сервер занят другим пользователем — консоль недоступна, пока бронь не снята.",
          hint: "Освободить сервер может владелец брони или администратор (Управление → Busy lease → Освободить принудительно).",
          normal: false,
        };
      }
      if (reason === "SERVER_DECOMMISSIONED") {
        return {
          message: "Сервер выведен из эксплуатации — консоль недоступна.",
          normal: false,
        };
      }
      if (reason === "ACCOUNT_HAS_NO_PASSWORD") {
        return {
          message: "У выбранной учётки нет сохранённого пароля.",
          hint: "Смените (ротируйте) пароль учётки и подключитесь снова.",
          normal: false,
        };
      }
      return {
        message: "Сервер сейчас не принимает консольную сессию.",
        hint: reason ? `Причина: ${reason}.` : undefined,
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
