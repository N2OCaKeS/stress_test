/**
 * Console-вкладка карточки сервера и ВМ — интерактивный терминал поверх
 * xterm.js. Разметка одна и та же: сверху выбор сервисной учётки, ниже живой
 * терминал. Карточка ВМ добавляет над этим блоком селектор вида консоли
 * (ssh/serial → тот же терминал; vnc/spice → графический прокси).
 *
 * Соединение: WebSocket на `/api/server/v1/servers/{id}/console/ws` (сервер)
 * или `/api/server/v1/vms/{id}/console/ws` (ВМ), в обоих случаях с query
 * `account_id=<acc>`. Консоль открывается под выбранной учёткой; access-токен
 * уходит subprotocol'ом `bearer.<token>` (см. `@/api/server/console`), потому
 * что браузерный WS не шлёт заголовков. После connect бэк поднимает PTY/ssh под
 * этим аккаунтом; ввод терминала уходит ws.send(text), вывод приходит
 * binary-кадрами и пишется в xterm как есть.
 *
 * Prepare для консоли не требуется — заходим напрямую под выбранным аккаунтом.
 * Обязателен лишь выбор учётки; причины отказа бэка маппятся на понятный баннер
 * по close-коду.
 *
 * Владение WebSocket'ом и xterm-Terminal'ом живёт НЕ в этом компоненте, а в
 * персистентном module-уровневом реестре (`@/lib/consoleSocketRegistry`) —
 * `ConsoleSession` на маунте либо создаёт новую запись (`connect()`), либо
 * находит уже живую по ключу `(target, account)` и просто переоткрывает
 * xterm в свой DOM-узел (`Terminal.open`), не трогая сокет. На unmount (уход
 * на другую страницу ВНУТРИ SPA) запись не закрывается — WS и Terminal живут
 * дальше сами по себе, пока эту же карточку не откроют снова (или сессия не
 * закроется по другой причине: «Отключить», logout, реальное закрытие
 * вкладки — тогда бэк получает распознаваемый close-код и не ждёт grace, см.
 * `console.py`). Detach/reattach через `session_id`+sessionStorage (ниже) —
 * отдельный, более медленный fallback-слой на случай, когда сам JS-реестр не
 * пережил разрыв (перезагрузка страницы, реальный обрыв сети, новая вкладка).
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  AlertCircle,
  Boxes,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  Maximize2,
  Minimize2,
  Plug,
  PlugZap,
  Radio,
  Terminal as TerminalIcon,
  TerminalSquare,
} from "lucide-react";
import { getAccount, listAccounts } from "@/api/server/accounts";
import {
  consoleWsProtocols,
  consoleWsUrl,
  describeConsoleClose,
  vmConsoleWsUrl,
  type ConsoleCloseInfo,
} from "@/api/server/console";
import {
  testLogStreamProtocols,
  testLogStreamUrl,
} from "@/api/testing/logStream";
import { findActiveQueueItemForServer } from "@/api/testing/testStands";
import type { QueueItemSummary } from "@/api/testing/types";
import { useQuery } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { fromBase64 } from "@/lib/base64";
import { filterAccessibleAccounts } from "@/pages/server/_serverShared";
import { ConsoleMacrosPanel } from "@/pages/server/tabs/ConsoleMacros";
import { HeightResizeHandle } from "@/components/shell/ResizeHandle";
import { usePanelHeight } from "@/components/shell/usePanelWidth";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Dropdown } from "@/components/ui/Dropdown";
import {
  CONSOLE_CLOSE_INTENTIONAL,
  attachSubscriber,
  consoleRegistryKey,
  detachSubscriber,
  findLiveAccountId,
  getConsoleEntry,
  registerConsoleEntry,
  removeConsoleEntry,
  type ConsoleSocketEntry,
  type ConsoleSocketSubscriber,
} from "@/lib/consoleSocketRegistry";
import { mockVmAccounts, mockVmConsole } from "@/mocks/vm";
import {
  alltaUpdateVm,
  listVmAccounts,
  openVmConsole,
  vmConsoleViewerUrl,
  type Vm,
  type VmAccount,
  type VmConsoleKind,
  type VmConsoleResponse,
} from "@/api/server/vms";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  Server,
  ServerAccount,
  TaskDispatchResponse,
} from "@/api/server/types";
import type { EntityRef } from "./_entity";
import { Button } from "@/components/ui/Button";

interface Props {
  serverId?: string;
  server?: Server;
  entity?: EntityRef;
}

type ConnState = "idle" | "connecting" | "open" | "closed";

/**
 * Куда открывать консольный WS. И сервер, и ВМ переиспользуют один терминал —
 * различается только маршрут, который выбирается по этому дескриптору.
 */
type ConsoleTarget =
  | { kind: "server"; serverId: string }
  | { kind: "vm"; vmId: string; consoleKind?: "ssh" | "serial" };

/**
 * Минимум, который picker'у и терминалу нужен от учётки. Серверный
 * `ServerAccount` подходит как есть; учётка ВМ приводится к этой форме.
 */
interface ConsoleAccount {
  id: string;
  login: string;
  has_sudo: boolean;
  /** Только у серверных учёток — помечаем discovered в списке. */
  source?: string;
}

// Ключ и границы для регулируемой высоты терминала. По умолчанию высота не
// зафиксирована — терминал заполняет всё доступное место; после первого drag'а
// в localStorage ложится явный оверрайд.
const CONSOLE_HEIGHT_KEY = "dbos-console-height";
const CONSOLE_MIN_HEIGHT = 240;
const CONSOLE_MAX_HEIGHT = 2000;

// ── Detach/reattach: session_id консоли переживает unmount компонента ───────
// (переход на другую страницу/вкладку внутри SPA, но не закрытие браузерной
// вкладки/reload — sessionStorage привязан к ней). Бэкенд держит PTY живым
// ещё грейс-период после обрыва WS (см. докстринг `server_service/.../
// console.py`) — если вернуться на эту же карточку и переподключиться под
// той же учёткой раньше, чем грейс истечёт, увидим тот же терминал, без
// повторного запуска сессии. История вывода за время разрыва не
// восстанавливается — только то, что PTY произведёт после реконнекта.
const CONSOLE_ACTIVE_SESSION_PREFIX = "dbos-console-active:";

interface PersistedConsoleSession {
  accountId: string;
  sessionId: string;
}

function consoleTargetStorageKey(target: ConsoleTarget): string {
  return target.kind === "server"
    ? `${CONSOLE_ACTIVE_SESSION_PREFIX}server:${target.serverId}`
    : `${CONSOLE_ACTIVE_SESSION_PREFIX}vm:${target.vmId}:${target.consoleKind ?? "ssh"}`;
}

function loadPersistedSession(target: ConsoleTarget): PersistedConsoleSession | null {
  try {
    const raw = sessionStorage.getItem(consoleTargetStorageKey(target));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedConsoleSession>;
    if (typeof parsed.accountId === "string" && typeof parsed.sessionId === "string") {
      return { accountId: parsed.accountId, sessionId: parsed.sessionId };
    }
  } catch {
    // приватный режим / битый JSON — просто нет сохранённой сессии
  }
  return null;
}

function savePersistedSession(
  target: ConsoleTarget,
  accountId: string,
  sessionId: string,
): void {
  try {
    sessionStorage.setItem(
      consoleTargetStorageKey(target),
      JSON.stringify({ accountId, sessionId } satisfies PersistedConsoleSession),
    );
  } catch {
    // квота/приватный режим — просто не переживём reload, не критично
  }
}

/** session_id, который стоит попробовать на reattach для (target, accountId),
 * либо `undefined`, если сохранённой сессии для ЭТОЙ учётки нет. */
function pickReattachSessionId(
  target: ConsoleTarget,
  accountId: string,
): string | undefined {
  const persisted = loadPersistedSession(target);
  return persisted && persisted.accountId === accountId
    ? persisted.sessionId
    : undefined;
}

/** Текстовый control-кадр `{"event":"session","session_id":...}` — единственный
 * JSON-текст, который шлёт мост при удачном connect/reattach. Всё остальное
 * текстовое от сервера в этом протоколе не встречается, но на всякий случай
 * не перехватываем ничего, что не распозналось как этот конкретный кадр. */
function tryParseSessionFrame(text: string): string | null {
  if (!text.startsWith("{")) return null;
  try {
    const obj: unknown = JSON.parse(text);
    if (
      obj && typeof obj === "object" &&
      (obj as Record<string, unknown>).event === "session" &&
      typeof (obj as Record<string, unknown>).session_id === "string"
    ) {
      return (obj as Record<string, unknown>).session_id as string;
    }
  } catch {
    // не JSON — обычный текстовый кадр
  }
  return null;
}

// Как часто спрашиваем testing_service, не идёт ли сейчас на этом сервере
// тест (§8.6 плана миграции) — обычный REST-polling, WS для самого
// обнаружения не нужен, он появляется только когда кнопка уже нажата.
const LIVE_LOG_POLL_INTERVAL_MS = 5000;

export function ConsoleTab({ serverId = "", server, entity }: Props) {
  // Карточка ВМ рендерит ту же вкладку, но с селектором вида консоли сверху.
  // Диспетчер без хуков — ветка фиксируется на монтирование.
  if (entity?.kind === "vm")
    return (
      <VmConsoleTab
        vm={entity.vm}
        mock={entity.mock}
        canManage={entity.canManage}
        onChanged={entity.onChanged}
      />
    );
  return <ServerConsoleTab serverId={serverId} server={server} />;
}

function ServerConsoleTab({ serverId = "", server }: Props) {
  const { persona } = usePersona();
  const accountsQ = useQuery(
    () => listAccounts({ server_id: serverId, limit: 200 }),
    [serverId],
  );

  const accounts = useMemo<ServerAccount[]>(() => {
    const data = accountsQ.data as
      | OffsetPaginatedResponse<ServerAccount>
      | CursorPaginatedResponse<ServerAccount>
      | undefined;
    return data?.items ?? [];
  }, [accountsQ.data]);

  const accessible = useMemo(
    () => filterAccessibleAccounts(accounts, persona),
    [accounts, persona],
  );

  // Грубый клиентский gate под право view_password — та же роль, что и для
  // reveal-пароля на вкладке аккаунтов (dep_admin / server admin / operator).
  // Финальное решение за backend'ом: без гранта `getAccount` вернёт
  // password_b64=null, и кнопка просто скажет «нет доступа».
  const canReveal =
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";

  const target = useMemo<ConsoleTarget>(
    () => ({ kind: "server", serverId }),
    [serverId],
  );

  // Живой лог теста (§8.6 плана миграции): пока идёт исполнение теста на этом
  // стенде, testing_service отдаёт активный queue_item — сигнал показать
  // кнопку. Обычный REST-polling, независимый от интерактивной SSH-сессии
  // ниже: обе панели могут быть открыты одновременно.
  const [activeQueueItem, setActiveQueueItem] =
    useState<QueueItemSummary | null>(null);
  useEffect(() => {
    if (!serverId) return;
    let cancelled = false;
    async function poll() {
      try {
        const item = await findActiveQueueItemForServer(serverId);
        if (!cancelled) setActiveQueueItem(item);
      } catch {
        // Сервер может не быть заведён как тестовый стенд вовсе (404) или
        // testing_service временно недоступен — в обоих случаях просто не
        // показываем кнопку, это не ошибка страницы консоли.
        if (!cancelled) setActiveQueueItem(null);
      }
    }
    poll();
    const timer = setInterval(poll, LIVE_LOG_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [serverId]);

  return (
    <div className="p-5 flex flex-col gap-4 w-full h-full min-h-0">
      <div>
        <div className="text-sm font-medium mb-1 flex items-center gap-2">
          <TerminalIcon className="w-4 h-4 text-accent" />
          SSH-консоль
        </div>
        <div className="text-xs text-dim">
          Подключение к серверу{" "}
          <span className="mono">
            {server?.display_name ?? server?.hostname ?? serverId}
          </span>{" "}
          под выбранной сервисной учёткой.
        </div>
      </div>

      {activeQueueItem && (
        <LiveTestLogSection queueItemId={activeQueueItem.queue_item_id} />
      )}

      {server?.busy_state === "testing" && (
        <div className="alert flex items-start gap-2">
          <Radio className="w-4 h-4 mt-0.5 text-accent" />
          <div className="flex-1 text-xs text-dim">
            Сейчас на стенде идёт тест. Консоль подключится под учёткой
            исполнения теста автоматически — выбор аккаунта ниже на это не
            влияет.
          </div>
        </div>
      )}

      <AccountConsolePanel
        target={target}
        accounts={accessible}
        loading={accountsQ.loading}
        error={accountsQ.error}
        onRetry={accountsQ.refetch}
        canReveal={canReveal}
        emptyHint={
          <>
            На этом сервере нет аккаунтов, к которым у вас есть доступ.
            {accounts.length > 0 && (
              <>
                {" "}
                Всего привязано: {accounts.length}. Запросите grant у
                администратора сервиса или департамента.
              </>
            )}
          </>
        }
      />
    </div>
  );
}

/**
 * Общий блок «picker учётки + живой терминал». Кормится уже отфильтрованным
 * списком доступных учёток; loading/error/пустой список рисуются здесь же.
 */
function AccountConsolePanel({
  target,
  accounts,
  loading,
  error,
  onRetry,
  canReveal,
  emptyHint,
}: {
  target: ConsoleTarget;
  accounts: ConsoleAccount[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  canReveal: boolean;
  emptyHint: ReactNode;
}) {
  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const selectedAccount =
    accounts.find((a) => a.id === selectedAccountId) ?? null;

  // Учётка живой сессии (см. `ConsoleSession.onConnStateChange`) — дропдаун
  // блокируется, пока она открыта/открывается: смена аккаунта в дропдауне не
  // переоткрывает сокет автоматически, а значит вкладка «Пароль» могла бы
  // тянуть пароль ДРУГОЙ (только что выбранной) учётки и слать его в РЕАЛЬНО
  // открытую сессию под старой — баг из аудита. Простой и однозначный фикс:
  // пока сессия жива, дропдаун недоступен — сначала отключитесь.
  const [sessionState, setSessionState] = useState<ConnState>("idle");
  const accountLocked = sessionState === "open" || sessionState === "connecting";

  // Однократный автовыбор учётки уже живой сессии — сначала смотрим в
  // persistent-реестр (WS реально ещё открыт где-то в этой же вкладке, просто
  // мы только что смонтировались на другой странице SPA), и только если там
  // пусто — на sessionStorage (grace-period fallback на реальный обрыв). Чтобы
  // при возврате на карточку не приходилось выбирать аккаунт заново вручную.
  const autoSelectedRef = useRef(false);
  useEffect(() => {
    if (autoSelectedRef.current || accounts.length === 0) return;
    autoSelectedRef.current = true;
    const accountIds = accounts.map((a) => a.id);
    const liveAccountId =
      target.kind === "server"
        ? findLiveAccountId("server", target.serverId, accountIds)
        : findLiveAccountId("vm", target.vmId, accountIds, target.consoleKind ?? "ssh");
    if (liveAccountId) {
      setSelectedAccountId(liveAccountId);
      return;
    }
    const persisted = loadPersistedSession(target);
    if (persisted && accounts.some((a) => a.id === persisted.accountId)) {
      setSelectedAccountId(persisted.accountId);
    }
  }, [accounts, target]);

  return (
    <>
      {loading && <div className="text-xs text-dim">Загружаем аккаунты…</div>}

      {!!error && (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(error, "Аккаунты не загрузились")}</div>
            <Button variant="ghost" className="mt-2" onClick={onRetry}>
              Повторить
            </Button>
          </div>
        </div>
      )}

      {!loading && !error && accounts.length === 0 && (
        <div className="alert flex items-start gap-2">
          <Lock className="w-4 h-4 mt-0.5 text-dim" />
          <div className="flex-1 text-xs text-dim">{emptyHint}</div>
        </div>
      )}

      {accounts.length > 0 && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            Аккаунт для подключения (обязательно)
          </span>
          <Dropdown
            mode="single"
            searchable
            disabled={accountLocked}
            placeholder="— выберите учётку —"
            options={[
              { value: "", label: "— выберите учётку —" },
              ...accounts.map((a) => ({
                value: a.id,
                label: `${a.login}${a.has_sudo ? " (sudo)" : ""}${a.source === "discovered" ? " · discovered" : ""}`,
              })),
            ]}
            value={selectedAccountId}
            onChange={setSelectedAccountId}
          />
          <span className="text-dim text-xs">
            {accountLocked
              ? "Сессия открыта под этим аккаунтом — отключитесь, чтобы сменить учётку."
              : "Сессия откроется под этим аккаунтом. Подготовка (Prepare) для консоли не требуется."}
          </span>
        </label>
      )}

      {accounts.length > 0 && (
        <ConsoleSession
          target={target}
          account={selectedAccount}
          canReveal={canReveal}
          onConnStateChange={setSessionState}
        />
      )}
    </>
  );
}

function StatusBadge({ state }: { state: ConnState }) {
  const map: Record<ConnState, { label: string; cls: string }> = {
    idle: { label: "Не подключено", cls: "text-dim" },
    connecting: { label: "Подключение…", cls: "text-accent" },
    open: { label: "Подключено", cls: "text-green-500" },
    closed: { label: "Отключено", cls: "text-dim" },
  };
  const { label, cls } = map[state];
  return <span className={`text-xs ${cls}`}>{label}</span>;
}

/**
 * Живой терминал. xterm монтируется один раз в контейнер; WebSocket
 * открывается по «Подключить» (или автоматически, если для этой пары
 * target+account есть сохранённая ещё живая сессия — см. reattach-эффект
 * ниже) и явно закрывается по «Отключить». На unmount WS НЕ закрывается
 * искусственно — компонент просто перестаёт существовать (переход на другую
 * страницу внутри SPA), браузер прибьёт соединение сам, а бэкенд даст
 * grace-период на переподключение вместо немедленного `stop` (см. докстринг
 * `console.py`). Маршрут WS выбирается по `target` (сервер или ВМ) — сама
 * механика одна и та же.
 */
function ConsoleSession({
  target,
  account,
  canReveal,
  onConnStateChange,
}: {
  target: ConsoleTarget;
  account: ConsoleAccount | null;
  canReveal: boolean;
  onConnStateChange?: (state: ConnState) => void;
}) {
  const toast = useToast();
  const mountRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const subscriberRef = useRef<ConsoleSocketSubscriber | null>(null);

  // Ключ persistent-реестра для (target, account) — null, пока аккаунт не
  // выбран. Смена аккаунта/вида консоли меняет ключ и заново прогоняет
  // attach-или-create эффект ниже (каждый ключ — своя, независимая сессия).
  const key = useMemo(() => {
    if (!account) return null;
    return target.kind === "server"
      ? consoleRegistryKey("server", target.serverId, account.id)
      : consoleRegistryKey("vm", target.vmId, account.id, target.consoleKind ?? "ssh");
  }, [target, account]);

  const [state, setState] = useState<ConnState>(
    () => (key && getConsoleEntry(key)?.state) || "idle",
  );
  const onConnStateChangeRef = useRef(onConnStateChange);
  onConnStateChangeRef.current = onConnStateChange;
  const updateState = useCallback((next: ConnState) => {
    setState(next);
    onConnStateChangeRef.current?.(next);
  }, []);
  const [closeInfo, setCloseInfo] = useState<ConsoleCloseInfo | null>(
    () => (key && getConsoleEntry(key)?.closeInfo) || null,
  );
  const [injecting, setInjecting] = useState(false);
  const [termHeight, setTermHeight] = usePanelHeight(
    CONSOLE_HEIGHT_KEY,
    CONSOLE_MIN_HEIGHT,
    CONSOLE_MAX_HEIGHT,
  );
  // Разворот на весь экран — чисто CSS-переключение того же дерева (терминал
  // не пересоздаётся): ResizeObserver внутри xterm-эффекта сам подхватывает
  // смену размера обёртки и пересчитывает fit, отдельно дёргать fit не нужно.
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    if (!fullscreen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  // Монтируем терминал (или переиспользуем уже живой из persistent-реестра —
  // см. модульный докстринг) в контейнер при каждой смене `key`. Fit — и на
  // ресайз окна, и на ресайз самого контейнера (смена вкладок/раскрытие
  // панелей меняют высоту не трогая window) — иначе xterm считает строки по
  // устаревшему размеру и нижний ряд клипается, а scrollback не прокручивается.
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const existing = key ? getConsoleEntry(key) : undefined;
    let term: Terminal;
    let fit: FitAddon;

    if (existing) {
      // Реюз: WS и Terminal уже живут в реестре (сессия пережила уход на
      // другую страницу SPA) — просто переоткрываем xterm в свой DOM-узел и
      // подписываемся на дальнейшие события. Никакого нового подключения.
      term = existing.term;
      fit = existing.fit;
      wsRef.current = existing.ws;
      sessionIdRef.current = existing.sessionId;
      const subscriber: ConsoleSocketSubscriber = {
        onState: updateState,
        onCloseInfo: setCloseInfo,
        onSessionId: (sid) => {
          sessionIdRef.current = sid;
        },
      };
      attachSubscriber(existing.key, subscriber);
      subscriberRef.current = subscriber;
      updateState(existing.state);
      setCloseInfo(existing.closeInfo);
      term.open(mount);
      try {
        fit.fit();
      } catch {
        // контейнер ещё без размеров — fit'нём на первом ресайзе
      }
    } else {
      term = new Terminal({
        convertEol: true,
        cursorBlink: true,
        scrollback: 5000,
        fontFamily:
          "'JetBrains Mono Variable', ui-monospace, SFMono-Regular, monospace",
        fontSize: 13,
        theme: { background: "#1e1e1e" },
      });
      fit = new FitAddon();
      term.loadAddon(fit);
      term.open(mount);
      try {
        fit.fit();
      } catch {
        // контейнер ещё без размеров — fit'нём на первом ресайзе
      }
    }

    termRef.current = term;
    fitRef.current = fit;

    const onResize = () => {
      try {
        fit.fit();
      } catch {
        // терминал мог быть уже dispose'нут
      }
    };
    window.addEventListener("resize", onResize);
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => onResize())
        : null;
    ro?.observe(mount);

    return () => {
      window.removeEventListener("resize", onResize);
      ro?.disconnect();
      if (key && subscriberRef.current) {
        detachSubscriber(key, subscriberRef.current);
        subscriberRef.current = null;
      }
      // Живая запись в реестре (по этому же ключу) значит, что сессия
      // продолжает жить сама по себе — переход на другую страницу SPA не
      // должен рвать ни WS, ни Terminal. Иначе (никогда не подключались,
      // либо сессия уже реально закрылась) — терминал был чисто локальным,
      // dispose'им как раньше.
      const stillLive = key ? getConsoleEntry(key) : undefined;
      if (!stillLive) {
        term.dispose();
      }
      termRef.current = null;
      fitRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const disconnect = useCallback(() => {
    const entry = key ? getConsoleEntry(key) : undefined;
    const ws = entry?.ws ?? wsRef.current;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      // 4001 — явное клиентское намерение прекратить сессию прямо сейчас
      // («Отключить», как и logout) — бэк не входит в grace на этот код.
      ws.close(CONSOLE_CLOSE_INTENTIONAL, "client_disconnect");
    }
    updateState("closed");
  }, [key, updateState]);

  const connect = useCallback(() => {
    const term = termRef.current;
    if (!term || !account || !key) return;
    // Защитный случай: живая запись уже есть (кнопка «Подключить» не должна
    // быть видна, пока сессия жива) — не пересоздаём поверх неё.
    if (getConsoleEntry(key)) return;

    setCloseInfo(null);
    updateState("connecting");
    term.clear();
    term.writeln("Подключение к консоли…");

    // Persistent-реестр уже проверили выше (пусто) — sessionStorage-reattach
    // остаётся fallback'ом на РЕАЛЬНЫЙ обрыв (reload страницы, новая вкладка),
    // который сам JS-реестр пережить не может. Бэкенд сам решает, валиден ли
    // ещё этот session_id; невалидный тихо игнорирует и создаёт новую сессию.
    const reattachSessionId = pickReattachSessionId(target, account.id);
    const url =
      target.kind === "server"
        ? consoleWsUrl(target.serverId, account.id, reattachSessionId)
        : vmConsoleWsUrl(target.vmId, account.id, target.consoleKind ?? "ssh", reattachSessionId);

    let ws: WebSocket;
    try {
      ws = new WebSocket(url, consoleWsProtocols());
    } catch {
      updateState("closed");
      setCloseInfo({
        message: "Не удалось открыть WebSocket-соединение.",
        normal: false,
      });
      return;
    }
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    const entry: ConsoleSocketEntry = {
      key,
      ws,
      term,
      fit: fitRef.current!,
      sessionId: null,
      state: "connecting",
      closeInfo: null,
      subscriber: null,
      inputDispose: null,
    };
    registerConsoleEntry(entry);
    const subscriber: ConsoleSocketSubscriber = {
      onState: updateState,
      onCloseInfo: setCloseInfo,
      onSessionId: (sid) => {
        sessionIdRef.current = sid;
      },
    };
    entry.subscriber = subscriber;
    subscriberRef.current = subscriber;

    // Обработчики WS живут на уровне записи реестра, не этого компонента:
    // они обязаны продолжать работать (писать вывод в term, обновлять
    // entry.state), даже когда компонент размонтирован — доставка в React
    // идёт исключительно через `entry.subscriber`, который есть только пока
    // кто-то смонтирован и смотрит.
    ws.onopen = () => {
      entry.state = "open";
      entry.subscriber?.onState("open");
      const sub = term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(data);
      });
      entry.inputDispose = () => sub.dispose();
      term.focus();
    };

    ws.onmessage = (ev) => {
      const data = ev.data;
      if (typeof data === "string") {
        // Единственный текстовый control-кадр в протоколе —
        // `{"event":"session","session_id":...}`, шлётся один раз сразу
        // после старта/reattach. Перехватываем его для sessionStorage-
        // fallback'а, не рисуем в терминале; всё остальное текстовое — как
        // раньше, обычный вывод.
        const sid = tryParseSessionFrame(data);
        if (sid) {
          entry.sessionId = sid;
          entry.subscriber?.onSessionId(sid);
          savePersistedSession(target, account.id, sid);
          return;
        }
        entry.term.write(data);
        return;
      }
      if (data instanceof ArrayBuffer) {
        entry.term.write(new Uint8Array(data));
      } else if (data instanceof Blob) {
        data.arrayBuffer().then((buf) => {
          entry.term.write(new Uint8Array(buf));
        });
      }
    };

    ws.onclose = (ev) => {
      const info = describeConsoleClose(ev.code, ev.reason);
      entry.state = "closed";
      entry.closeInfo = info;
      entry.inputDispose?.();
      entry.inputDispose = null;
      entry.subscriber?.onState("closed");
      entry.subscriber?.onCloseInfo(info);
      try {
        entry.term.writeln(`\r\n\x1b[2m— ${info.message}\x1b[0m`);
      } catch {
        // term мог быть уже dispose'нут где-то ещё — не критично
      }
      removeConsoleEntry(key);
      if (wsRef.current === ws) wsRef.current = null;
    };

    ws.onerror = () => {
      // Детали придут в onclose (код/причина); здесь только не залипнуть в
      // «connecting», если соединение упало до open.
      if (entry.state === "connecting") {
        entry.state = "closed";
        entry.subscriber?.onState("closed");
      }
    };
  }, [target, account, key, updateState]);

  // Автоподключение при монтировании — только если в persistent-реестре для
  // этого ключа пусто (иначе mount-эффект выше уже атачнулся напрямую) и
  // есть сохранённый в sessionStorage session_id (реальный обрыв/reload, а
  // не обычная SPA-навигация). Срабатывает один раз на маунт, как только
  // появился аккаунт (авто-выбранный родителем — см. `AccountConsolePanel`).
  const autoConnectedRef = useRef(false);
  useEffect(() => {
    if (autoConnectedRef.current || !account || !key) return;
    autoConnectedRef.current = true;
    if (getConsoleEntry(key)) return;
    if (pickReattachSessionId(target, account.id)) {
      connect();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account, key]);

  // Подставить пароль текущей учётки в терминал без перевода строки: при
  // запросе пароля (sudo и т.п.) пользователю остаётся нажать Enter. Пароль
  // достаём свежим запросом и держим в локальной переменной ровно на время
  // отправки — ни в state, ни в term.write (sudo не эхоит, не светим на экран).
  // Только для серверных учёток: у ВМ reveal-эндпоинта нет, кнопка задизейблена.
  const injectPassword = useCallback(async () => {
    const ws = wsRef.current;
    if (!account || injecting) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    setInjecting(true);
    try {
      const fresh = await getAccount(account.id);
      if (fresh.password_b64 === null) {
        toast.warn("Нет доступа к паролю этой учётки");
        return;
      }
      let secret: string;
      try {
        secret = fromBase64(fresh.password_b64);
      } catch {
        secret = fresh.password_b64;
      }
      ws.send(secret);
      toast.info("Пароль введён, нажмите Enter");
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        const secs = e.retryAfter ?? 60;
        toast.error(`Reveal-лимит: повторите через ${secs} сек`);
      } else if (e instanceof ApiError && e.status === 403) {
        toast.warn("Нет доступа к паролю этой учётки");
      } else {
        toast.error(apiErrMsg(e, "Не удалось получить пароль"));
      }
    } finally {
      setInjecting(false);
    }
  }, [account, injecting, toast]);

  // Выполнить команду макроса: шлём текст с переводом строки — как будто
  // пользователь набрал её и нажал Enter. Только при открытой сессии.
  const runCommand = useCallback((commandText: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(commandText + "\n");
    termRef.current?.focus();
  }, []);

  const connected = state === "open" || state === "connecting";
  const sessionOpen = state === "open";

  return (
    <div
      className={
        fullscreen
          ? "fixed inset-0 z-[1000] flex flex-col gap-3 p-4 surface"
          : "flex flex-col gap-3 flex-1 min-h-0"
      }
    >
      <div className="flex items-center gap-3">
        {connected ? (
          <Button variant="ghost" onClick={disconnect}>
            <PlugZap className="w-4 h-4" />
            Отключить
          </Button>
        ) : (
          <Button variant="primary"
            onClick={connect}
            disabled={!account}
            title={account ? "Открыть консольную сессию" : "Выберите аккаунт"}
          >
            <Plug className="w-4 h-4" />
            Подключить
          </Button>
        )}
        {sessionOpen && (
          <Button variant="ghost"
            onClick={injectPassword}
            disabled={!canReveal || injecting}
            title={
              canReveal
                ? "Ввести пароль учётки в терминал (без Enter)"
                : "Нет доступа к паролю"
            }
          >
            <KeyRound className="w-4 h-4" />
            Пароль
          </Button>
        )}
        <StatusBadge state={state} />
        {account && (
          <span className="text-xs text-dim mono">
            {account.login}
            {account.has_sudo ? " (sudo)" : ""}
          </span>
        )}
        <Button
          variant="ghost"
          className="ml-auto"
          onClick={() => setFullscreen((v) => !v)}
          title={fullscreen ? "Свернуть консоль (Esc)" : "Развернуть консоль на весь экран"}
        >
          {fullscreen ? (
            <Minimize2 className="w-4 h-4" />
          ) : (
            <Maximize2 className="w-4 h-4" />
          )}
        </Button>
      </div>

      {closeInfo && !closeInfo.normal && (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{closeInfo.message}</div>
            {closeInfo.hint && (
              <div className="text-dim mt-1">{closeInfo.hint}</div>
            )}
          </div>
        </div>
      )}

      {sessionOpen && <ConsoleMacrosPanel onRun={runCommand} />}

      {/* Паддинг/фон держим на обёртке, а xterm монтируем в дочерний div на всю
          высоту: иначе внутренний padding съедает измеряемую область, fit
          считает на ряд больше и низ терминала обрезается. Без явной высоты
          обёртка тянется на всё оставшееся место (flex-1); после drag'а высота
          фиксируется. Смену размера подхватывает ResizeObserver внутри и
          пересчитывает fit — терминал при этом не пересоздаётся. */}
      <div
        ref={wrapRef}
        className={`border border-token rounded overflow-hidden ${
          fullscreen || termHeight == null ? "flex-1 min-h-0" : ""
        }`}
        style={{
          // В fullscreen игнорируем запомненную высоту — терминал должен
          // занимать весь развёрнутый экран, а не сохранённые из обычного
          // режима пиксели.
          height: fullscreen ? undefined : termHeight ?? undefined,
          background: "#1e1e1e",
          padding: 8,
        }}
      >
        <div ref={mountRef} style={{ height: "100%", width: "100%" }} />
      </div>
      {!fullscreen && (
        <HeightResizeHandle
          min={CONSOLE_MIN_HEIGHT}
          max={CONSOLE_MAX_HEIGHT}
          measure={() =>
            wrapRef.current?.getBoundingClientRect().height ??
            termHeight ??
            CONSOLE_MIN_HEIGHT
          }
          onChange={setTermHeight}
          onReset={() => setTermHeight(null)}
          ariaLabel="Изменить высоту консоли (двойной клик — сброс)"
        />
      )}
    </div>
  );
}

// ── живой лог теста (§8.6 плана миграции) ───────────────────────────────────

/**
 * Кнопка «Живой лог теста» + read-only терминал под ней. Полностью
 * независим от интерактивной SSH-сессии выше: свой xterm, свой WebSocket,
 * своё состояние — оператор может держать открытыми оба сразу, это два
 * разных представления одного стенда, не конфликт.
 */
function LiveTestLogSection({ queueItemId }: { queueItemId: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="flex flex-col gap-2">
      <Button
        variant={open ? "primary" : "default"}
        onClick={() => setOpen((v) => !v)}
        className="self-start flex items-center gap-1"
        title="Показать вывод исполняющегося сейчас на этом стенде теста в реальном времени"
      >
        <Radio className="w-4 h-4" />
        {open ? "Скрыть живой лог теста" : "Живой лог теста"}
      </Button>
      {open && <LiveTestLogTerminal queueItemId={queueItemId} />}
    </div>
  );
}

/**
 * Read-only терминал живого лога. Открывает `WS /queue-items/{id}/log/stream`
 * сразу при монтировании — отдельной кнопки «подключить», в отличие от
 * интерактивной консоли, здесь не нужно: панель уже открыта явным кликом.
 * Ввод не принимается (`disableStdin`), автопереподключения при закрытии
 * WS сервером (тест завершился/соединение упало) нет — сообщение об этом
 * просто дописывается в сам терминал.
 */
function LiveTestLogTerminal({ queueItemId }: { queueItemId: string }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const [state, setState] = useState<ConnState>("idle");
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    if (!fullscreen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const term = new Terminal({
      convertEol: true,
      disableStdin: true,
      cursorBlink: false,
      scrollback: 10000,
      fontFamily:
        "'JetBrains Mono Variable', ui-monospace, SFMono-Regular, monospace",
      fontSize: 13,
      theme: { background: "#1e1e1e" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(mount);
    try {
      fit.fit();
    } catch {
      // контейнер ещё без размеров — fit'нём на первом ресайзе
    }
    termRef.current = term;

    const onResize = () => {
      try {
        fit.fit();
      } catch {
        // терминал мог быть уже dispose'нут
      }
    };
    window.addEventListener("resize", onResize);
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => onResize())
        : null;
    ro?.observe(mount);

    setState("connecting");
    term.writeln("Подключение к живому логу теста…");

    let ws: WebSocket;
    try {
      ws = new WebSocket(testLogStreamUrl(queueItemId), testLogStreamProtocols());
    } catch {
      setState("closed");
      term.writeln(
        "\r\n\x1b[2m— Не удалось открыть WebSocket-соединение.\x1b[0m",
      );
      return () => {
        window.removeEventListener("resize", onResize);
        ro?.disconnect();
        term.dispose();
        termRef.current = null;
      };
    }
    wsRef.current = ws;

    ws.onopen = () => setState("open");

    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") termRef.current?.write(ev.data);
    };

    ws.onclose = (ev) => {
      if (wsRef.current === ws) wsRef.current = null;
      setState("closed");
      const t = termRef.current;
      if (!t) return;
      if (ev.reason === "TEST_FINISHED") {
        t.writeln("\r\n\x1b[2m— Тест завершён.\x1b[0m");
      } else if (ev.code === 1000) {
        t.writeln("\r\n\x1b[2m— Соединение закрыто.\x1b[0m");
      } else {
        const tail = ev.reason ? `, ${ev.reason}` : "";
        t.writeln(`\r\n\x1b[2m— Соединение закрыто (код ${ev.code}${tail}).\x1b[0m`);
      }
    };

    ws.onerror = () => {
      // Детали придут в onclose; здесь только не залипнуть в «connecting»,
      // если соединение упало до open.
      setState((s) => (s === "connecting" ? "closed" : s));
    };

    return () => {
      window.removeEventListener("resize", onResize);
      ro?.disconnect();
      wsRef.current?.close(1000, "unmount");
      wsRef.current = null;
      term.dispose();
      termRef.current = null;
    };
  }, [queueItemId]);

  return (
    <div
      className={
        fullscreen
          ? "fixed inset-0 z-[1000] flex flex-col gap-3 p-4 surface"
          : "flex flex-col gap-2"
      }
    >
      <div className="flex items-center gap-3">
        <StatusBadge state={state} />
        <span className="text-xs text-dim">
          Только чтение — вывод исполняющегося сейчас теста
        </span>
        <Button
          variant="ghost"
          className="ml-auto"
          onClick={() => setFullscreen((v) => !v)}
          title={fullscreen ? "Свернуть (Esc)" : "Развернуть на весь экран"}
        >
          {fullscreen ? (
            <Minimize2 className="w-4 h-4" />
          ) : (
            <Maximize2 className="w-4 h-4" />
          )}
        </Button>
      </div>
      <div
        ref={wrapRef}
        className={`border border-token rounded overflow-hidden ${
          fullscreen ? "flex-1 min-h-0" : ""
        }`}
        style={{
          height: fullscreen ? undefined : 320,
          background: "#1e1e1e",
          padding: 8,
        }}
      >
        <div ref={mountRef} style={{ height: "100%", width: "100%" }} />
      </div>
    </div>
  );
}

// ── консоль ВМ ──────────────────────────────────────────────────────────────

function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
}

/**
 * Консоль ВМ: сверху селектор вида (ssh по умолчанию / vnc / serial / spice).
 * ssh и serial используют тот же account-picker + живой терминал, что и сервер;
 * vnc/spice дают кнопку открытия графического прокси. Кнопка «Обновить allta»
 * — управляющее действие, доступно носителю права управления.
 */
function VmConsoleTab({
  vm,
  mock,
  canManage = false,
  onChanged,
}: {
  vm: Vm;
  mock: boolean;
  canManage?: boolean;
  onChanged?: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const [kind, setKind] = useState<VmConsoleKind>("ssh");
  const [alltaPending, setAlltaPending] = useState(false);

  const accountsQ = useQuery<VmAccount[]>(
    () => (mock ? Promise.resolve(mockVmAccounts(vm)) : listVmAccounts(vm.id)),
    [vm.id, mock],
  );

  // Учётки ВМ приводим к общей форме picker'а. Персона-фильтра у ВМ нет —
  // доступность режет backend при открытии сессии; reveal-пароля тоже нет.
  const accounts = useMemo<ConsoleAccount[]>(
    () =>
      (accountsQ.data ?? []).map((a) => ({
        id: a.account_id,
        login: a.login,
        has_sudo: a.has_sudo,
      })),
    [accountsQ.data],
  );

  const target = useMemo<ConsoleTarget>(
    () => ({ kind: "vm", vmId: vm.id }),
    [vm.id],
  );

  async function handleAllta() {
    const ok = await confirm({
      title: "Обновить allta",
      message: `Обновить guest-allta на ВМ ${vm.name}? Пройдёт по всем не-«_build» снимкам, переустановит .deb и переснимет их.`,
      confirmLabel: "Обновить",
    });
    if (!ok) return;
    setAlltaPending(true);
    try {
      const res = mock ? fakeDispatch() : await alltaUpdateVm(vm.id);
      toast.success(
        `Обновление allta ${vm.name} — задача поставлена (${res.task_id})`,
      );
      onChanged?.();
    } catch (e) {
      toast.error(apiErrMsg(e, "Обновление allta не удалось"));
    } finally {
      setAlltaPending(false);
    }
  }

  const kinds: { value: VmConsoleKind; label: string }[] = [
    { value: "ssh", label: "SSH" },
    { value: "vnc", label: "VNC" },
    { value: "serial", label: "Serial" },
    { value: "spice", label: "SPICE" },
  ];

  const isTerminal = kind === "ssh" || kind === "serial";

  return (
    <div className="p-5 flex flex-col gap-4 w-full h-full min-h-0">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <div className="text-sm font-medium mb-1 flex items-center gap-2">
            <TerminalSquare className="w-4 h-4 text-accent" />
            Консоль
          </div>
          <div className="text-xs text-dim">
            Подключение к ВМ{" "}
            <span className="mono">{vm.name}</span> под выбранной учёткой.
          </div>
        </div>
        {canManage && (
          <Button size="sm"
            type="button"
            className="flex items-center gap-1"
            onClick={handleAllta}
            disabled={alltaPending}
            title="Переустановить guest-allta по не-«_build» снимкам"
          >
            <Boxes className="w-3.5 h-3.5" />
            {alltaPending ? "Ставим задачу…" : "Обновить allta"}
          </Button>
        )}
      </div>

      <div className="flex items-center gap-1 flex-wrap">
        {kinds.map((k) => (
          <Button
            key={k.value}
            type="button"
            size="sm"
            variant={kind === k.value ? "primary" : "default"}
            onClick={() => setKind(k.value)}
          >
            {k.label}
          </Button>
        ))}
      </div>

      {isTerminal ? (
        <AccountConsolePanel
          target={
            target.kind === "vm"
              ? { ...target, consoleKind: kind === "serial" ? "serial" : "ssh" }
              : target
          }
          accounts={accounts}
          loading={accountsQ.loading}
          error={accountsQ.error}
          onRetry={accountsQ.refetch}
          canReveal={false}
          emptyHint={
            <>К этой ВМ не привязано ни одной учётки для консольного входа.</>
          }
        />
      ) : (
        <VmGraphicalConsole vm={vm} kind={kind} mock={mock} />
      )}
    </div>
  );
}

/**
 * Графическая консоль ВМ (vnc/spice) через self-hosted прокси. Вьювер (noVNC/
 * spice-html5) прокси отдаёт по GET и шлёт `frame-ancestors 'self'`, поэтому
 * встраиваем его same-origin iframe'ом прямо в рабочую область: по «Подключиться»
 * получаем сессию у бэка и рендерим вьювер инлайн. Адрес строим от текущего
 * origin (см. `vmConsoleViewerUrl`), а не от зашитого в ответе хоста.
 */
function VmGraphicalConsole({
  vm,
  kind,
  mock,
}: {
  vm: Vm;
  kind: VmConsoleKind;
  mock: boolean;
}) {
  const toast = useToast();
  const [session, setSession] = useState<VmConsoleResponse | null>(null);
  const [pending, setPending] = useState(false);

  // Смена вида сбрасывает уже полученную сессию.
  useEffect(() => {
    setSession(null);
  }, [kind]);

  async function connect() {
    setPending(true);
    try {
      const res = mock
        ? mockVmConsole(vm, kind)
        : await openVmConsole(vm.id, kind);
      setSession(res);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось получить данные консоли"));
    } finally {
      setPending(false);
    }
  }

  const proto = kind === "vnc" ? "VNC" : "SPICE";
  const viewerUrl = session ? vmConsoleViewerUrl(session) : null;

  return (
    <div className="flex flex-col gap-3">
      <Button variant="primary" size="sm"
        type="button"
        className="flex items-center gap-1 self-start"
        onClick={connect}
        disabled={pending}
      >
        <TerminalSquare className="w-3.5 h-3.5" />
        {pending
          ? "Подключаемся…"
          : session
            ? `Переподключить ${proto}`
            : `Подключиться (${proto})`}
      </Button>

      {!session && (
        <div className="text-xs text-dim">
          Нажмите «Подключиться», чтобы запустить графическую консоль {proto}{" "}
          прямо в рабочей области.
        </div>
      )}

      {session && (
        <div className="surface-2 border border-token rounded p-3 flex flex-col gap-2">
          <div className="text-xs text-dim">
            Графическая консоль ({proto}) через self-hosted прокси
            (noVNC/spice-html5).
          </div>
          {viewerUrl ? (
            <iframe
              src={viewerUrl}
              title={`Консоль ${proto} ВМ ${vm.name}`}
              className="w-full border border-token rounded bg-black"
              style={{ minHeight: 480 }}
            />
          ) : (
            <div className="border border-dashed border-token rounded p-4 text-center bg-black/5 dark:bg-white/5">
              <TerminalSquare className="w-8 h-8 mx-auto text-dim mb-2" />
              <div className="text-xs text-warn">
                Прокси-эндпоинт разворачивается инфраструктурно. Консоль появится
                после его поднятия.
              </div>
            </div>
          )}
          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1 text-xs">
            <Field
              k="host (hub)"
              v={`${session.host ?? "—"}${session.port ? `:${session.port}` : ""}`}
              mono
            />
            {session.ws_url && <Field k="ws-прокси" v={session.ws_url} mono />}
            <Field k="Токен" v={session.token} mono secret />
            {session.password && (
              <Field k={`Пароль ${proto}`} v={session.password} mono secret />
            )}
          </dl>
        </div>
      )}
    </div>
  );
}

function Field({
  k,
  v,
  mono,
  secret,
}: {
  k: string;
  v: string;
  mono?: boolean;
  secret?: boolean;
}) {
  // Токен/пароль VNC-SPICE сессии не так чувствителен, как SSH-пароль
  // долгоживущей учётки (см. injectPassword ниже), поэтому без серверного
  // throttle — просто прячем от шаринга экрана и скриншотов по умолчанию.
  const [shown, setShown] = useState(false);
  const masked = secret && !shown;

  return (
    <>
      <dt className="text-dim text-xs">{k}</dt>
      <dd className={mono ? "mono" : undefined}>
        {secret ? (
          <span className="inline-flex items-center gap-1.5">
            <span className={masked ? "select-none" : "break-all"}>
              {masked ? "••••••••••••" : v}
            </span>
            <button
              type="button"
              onClick={() => setShown((prev) => !prev)}
              className="text-dim hover:text-fg shrink-0"
              title={masked ? "Показать" : "Скрыть"}
            >
              {masked ? (
                <Eye className="w-3.5 h-3.5" />
              ) : (
                <EyeOff className="w-3.5 h-3.5" />
              )}
            </button>
          </span>
        ) : (
          v
        )}
      </dd>
    </>
  );
}
