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
  KeyRound,
  Lock,
  Maximize2,
  Minimize2,
  Plug,
  PlugZap,
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
            Сессия откроется под этим аккаунтом. Подготовка (Prepare) для
            консоли не требуется.
          </span>
        </label>
      )}

      {accounts.length > 0 && (
        <ConsoleSession
          target={target}
          account={selectedAccount}
          canReveal={canReveal}
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
 * открывается по «Подключить» и dispose'ится по «Отключить» / unmount. Маршрут
 * WS выбирается по `target` (сервер или ВМ) — сама механика одна и та же.
 */
function ConsoleSession({
  target,
  account,
  canReveal,
}: {
  target: ConsoleTarget;
  account: ConsoleAccount | null;
  canReveal: boolean;
}) {
  const toast = useToast();
  const mountRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const inputDisposeRef = useRef<(() => void) | null>(null);

  const [state, setState] = useState<ConnState>("idle");
  const [closeInfo, setCloseInfo] = useState<ConsoleCloseInfo | null>(null);
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

  // Монтируем терминал один раз и держим до unmount. fit и на ресайз окна, и
  // на ресайз самого контейнера (смена вкладок/раскрытие панелей меняют высоту
  // не трогая window) — иначе xterm считает строки по устаревшему размеру и
  // нижний ряд клипается, а scrollback не прокручивается.
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      scrollback: 5000,
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
      inputDisposeRef.current?.();
      inputDisposeRef.current = null;
      wsRef.current?.close(1000, "unmount");
      wsRef.current = null;
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  const disconnect = useCallback(() => {
    inputDisposeRef.current?.();
    inputDisposeRef.current = null;
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.close(1000, "client_disconnect");
    }
    setState("closed");
  }, []);

  const connect = useCallback(() => {
    const term = termRef.current;
    if (!term || !account) return;
    // Перед новым подключением убираем прошлый сокет/подписку.
    inputDisposeRef.current?.();
    inputDisposeRef.current = null;
    wsRef.current?.close(1000, "reconnect");
    wsRef.current = null;

    setCloseInfo(null);
    setState("connecting");
    term.clear();
    term.writeln("Подключение к консоли…");

    const url =
      target.kind === "server"
        ? consoleWsUrl(target.serverId, account.id)
        : vmConsoleWsUrl(target.vmId, account.id, target.consoleKind ?? "ssh");

    let ws: WebSocket;
    try {
      ws = new WebSocket(url, consoleWsProtocols());
    } catch {
      setState("closed");
      setCloseInfo({
        message: "Не удалось открыть WebSocket-соединение.",
        normal: false,
      });
      return;
    }
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setState("open");
      // Ввод терминала → серверу. xterm отдаёт строку (включая управляющие
      // последовательности), шлём как текстовый кадр.
      const sub = term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(data);
      });
      inputDisposeRef.current = () => sub.dispose();
      term.focus();
    };

    ws.onmessage = (ev) => {
      const t = termRef.current;
      if (!t) return;
      const data = ev.data;
      if (typeof data === "string") {
        t.write(data);
      } else if (data instanceof ArrayBuffer) {
        t.write(new Uint8Array(data));
      } else if (data instanceof Blob) {
        data.arrayBuffer().then((buf) => {
          termRef.current?.write(new Uint8Array(buf));
        });
      }
    };

    ws.onclose = (ev) => {
      if (wsRef.current === ws) wsRef.current = null;
      inputDisposeRef.current?.();
      inputDisposeRef.current = null;
      setState("closed");
      const info = describeConsoleClose(ev.code, ev.reason);
      setCloseInfo(info);
      const t = termRef.current;
      if (t) t.writeln(`\r\n\x1b[2m— ${info.message}\x1b[0m`);
    };

    ws.onerror = () => {
      // Детали придут в onclose (код/причина); здесь только не залипнуть в
      // «connecting», если соединение упало до open.
      if (state === "connecting") setState("closed");
    };
  }, [target, account, state]);

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
            <Field k="Токен" v={session.token} mono />
            {session.password && (
              <Field k={`Пароль ${proto}`} v={session.password} mono />
            )}
          </dl>
        </div>
      )}
    </div>
  );
}

function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-dim text-xs">{k}</dt>
      <dd className={mono ? "mono" : undefined}>{v}</dd>
    </>
  );
}
