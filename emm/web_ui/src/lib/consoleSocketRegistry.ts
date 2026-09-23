/**
 * Персистентный реестр живых WebSocket'ов интерактивной консоли (сервер/ВМ).
 *
 * Модуль — ES-module singleton: одна и та же `Map` живёт, пока жива вкладка
 * браузера, НЕЗАВИСИМО от того, что рендерит react-router в данный момент.
 * Переход между страницами внутри SPA (карточка сервера A → карточка сервера
 * B → снова A) не трогает записи здесь вообще: `ConsoleTab`
 * (`pages/server/tabs/console.tsx`) на маунте смотрит в реестр по ключу
 * `(targetType, targetId, kind, accountId)`; если запись уже есть — просто
 * переподключает xterm-Terminal к своему DOM-узлу (`Terminal.open`) и
 * подписывается на дальнейшие события, БЕЗ нового WebSocket-подключения и
 * БЕЗ reattach-хендшейка. На unmount запись не трогается — WS и Terminal
 * продолжают жить и получать вывод, даже когда никто их не смотрит.
 *
 * Почему не React Context: `closeAllConsoleSockets` должен быть вызываем из
 * `AuthContext` (logout, принудительный signout при истёкшем refresh) — это
 * plain-функция, не компонент, и React Context недоступен вне дерева
 * рендера без прокидывания через провайдеры, которых между `AuthProvider` и
 * этим модулем нет и не должно быть. Module-singleton решает это без единой
 * лишней абстракции и переживает SPA-роутинг ничуть не хуже (даже надёжнее)
 * контекста, подвешенного на конкретный компонент.
 *
 * Что НЕ входит в зону ответственности этого модуля: реальное закрытие
 * вкладки браузера / переход на другой сайт — WS в этом случае рвётся самим
 * браузером (со close-кодом 1001, "going away"), реестр просто перестаёт
 * существовать вместе со всем JS-состоянием страницы — специально закрывать
 * WS на этот случай не нужно и невозможно (некому исполнить код). Обычное
 * переключение вкладок БРАУЗЕРА (фокус ушёл на другую вкладку, emm не
 * закрыта) вообще не генерит никаких браузерных событий, которые стоило бы
 * здесь слушать — `visibilitychange` намеренно НЕ используется нигде в этом
 * модуле, иначе переключение фокуса ошибочно рвало бы живые сессии.
 */
import type { Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import type { ConsoleCloseInfo } from "@/api/server/console";

export type ConsoleConnState = "idle" | "connecting" | "open" | "closed";

/** Кто сейчас "смотрит" на запись реестра — обычно 0 (никто, вкладка
 * консоли где-то в другом месте SPA) или 1 (смонтированный компонент). */
export interface ConsoleSocketSubscriber {
  onState(state: ConsoleConnState): void;
  onCloseInfo(info: ConsoleCloseInfo | null): void;
  onSessionId(sessionId: string): void;
}

export interface ConsoleSocketEntry {
  key: string;
  ws: WebSocket;
  term: Terminal;
  fit: FitAddon;
  sessionId: string | null;
  state: ConsoleConnState;
  closeInfo: ConsoleCloseInfo | null;
  subscriber: ConsoleSocketSubscriber | null;
  /** Отписка `term.onData` от текущего WS — живёт на записи, не на
   * компоненте: подписка привязана к жизни сокета, а не к тому, смонтирован
   * ли сейчас кто-то и смотрит на терминал (пока никто не смотрит, xterm
   * физически не получает пользовательский ввод — контейнер вне DOM). */
  inputDispose: (() => void) | null;
}

const registry = new Map<string, ConsoleSocketEntry>();

/** Ключ реестра — сервер/ВМ + вид консоли (ssh/serial, только у ВМ) + аккаунт.
 * Разные аккаунты на одном сервере — разные сессии, держим их параллельно. */
export function consoleRegistryKey(
  targetType: "server" | "vm",
  targetId: string,
  accountId: string,
  kind?: string,
): string {
  return kind
    ? `${targetType}:${targetId}:${kind}:${accountId}`
    : `${targetType}:${targetId}:${accountId}`;
}

export function getConsoleEntry(key: string): ConsoleSocketEntry | undefined {
  return registry.get(key);
}

export function registerConsoleEntry(entry: ConsoleSocketEntry): void {
  registry.set(entry.key, entry);
}

export function removeConsoleEntry(key: string): void {
  registry.delete(key);
}

/** Есть ли для (targetType, targetId, kind) живая/открывающаяся сессия под
 * каким-нибудь из перечисленных account'ов — используется автопиком учётки
 * при маунте, чтобы сразу показать ту, что уже подключена. */
export function findLiveAccountId(
  targetType: "server" | "vm",
  targetId: string,
  accountIds: string[],
  kind?: string,
): string | null {
  for (const id of accountIds) {
    const entry = registry.get(consoleRegistryKey(targetType, targetId, id, kind));
    if (entry && (entry.state === "open" || entry.state === "connecting")) {
      return id;
    }
  }
  return null;
}

export function attachSubscriber(
  key: string,
  subscriber: ConsoleSocketSubscriber,
): void {
  const entry = registry.get(key);
  if (entry) entry.subscriber = subscriber;
}

export function detachSubscriber(
  key: string,
  subscriber: ConsoleSocketSubscriber,
): void {
  const entry = registry.get(key);
  if (entry && entry.subscriber === subscriber) entry.subscriber = null;
}

/** Close-код, которым фронт сигналит бэку «это осознанное завершение, не
 * жди reattach» — кнопка «Отключить» и logout/signout ниже. Бэк (`console.py`,
 * `_CLIENT_CLOSE_INTENTIONAL`) публикует `stop` немедленно на этот код, минуя
 * grace-период (в отличие от кода 1001 "going away", 4001 наш собственный —
 * его шлём только мы сами, явно, а не браузер на unload). */
export const CONSOLE_CLOSE_INTENTIONAL = 4001;

/**
 * Разорвать ВСЕ живые консольные WS немедленно (logout, принудительный
 * signout при отозванной сессии). Вызывается из `AuthContext`, не из React-
 * дерева консоли — оставлять сессии висеть под чужим (следующим залогиненным)
 * пользователем в этой же вкладке недопустимо, поэтому здесь нет grace и нет
 * исключений «а вдруг переподключятся».
 */
export function closeAllConsoleSockets(): void {
  for (const [key, entry] of registry) {
    try {
      entry.inputDispose?.();
    } catch {
      // no-op
    }
    try {
      entry.ws.close(CONSOLE_CLOSE_INTENTIONAL, "logout");
    } catch {
      // сокет уже мог быть в CLOSING/CLOSED — не критично
    }
    try {
      entry.term.dispose();
    } catch {
      // no-op
    }
    registry.delete(key);
  }
}
