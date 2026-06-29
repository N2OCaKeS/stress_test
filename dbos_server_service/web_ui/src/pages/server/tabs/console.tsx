/**
 * Console-вкладка карточки сервера — интерактивный терминал поверх xterm.js.
 *
 * Соединение: WebSocket на
 * `/api/server/v1/servers/{id}/console/ws?account_id=<acc>`. Консоль
 * открывается под выбранной сервисной учёткой — её id уходит в query
 * `account_id`, а access-токен передаётся subprotocol'ом `bearer.<token>`
 * (см. `@/api/server/console`), потому что браузерный WS не шлёт заголовков.
 * После connect бэк сам поднимает PTY под этим аккаунтом; ввод терминала
 * уходит ws.send(text), вывод приходит binary-кадрами и пишется в xterm как
 * есть.
 *
 * Prepare для консоли не требуется — заходим напрямую под выбранным
 * аккаунтом. Обязателен лишь выбор учётки; причины отказа бэка маппятся на
 * понятный баннер по close-коду.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  AlertCircle,
  KeyRound,
  Lock,
  Plug,
  PlugZap,
  Terminal as TerminalIcon,
} from "lucide-react";
import { getAccount, listAccounts } from "@/api/server/accounts";
import {
  consoleWsProtocols,
  consoleWsUrl,
  describeConsoleClose,
  type ConsoleCloseInfo,
} from "@/api/server/console";
import { useQuery } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { fromBase64 } from "@/lib/base64";
import { filterAccessibleAccounts } from "@/pages/server/_serverShared";
import { ConsoleMacrosPanel } from "@/pages/server/tabs/ConsoleMacros";
import type {
  CursorPaginatedResponse,
  OffsetPaginatedResponse,
  Server,
  ServerAccount,
} from "@/api/server/types";

interface Props {
  serverId: string;
  server?: Server;
}

type ConnState = "idle" | "connecting" | "open" | "closed";

export function ConsoleTab({ serverId, server }: Props) {
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

  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const selectedAccount =
    accessible.find((a) => a.id === selectedAccountId) ?? null;

  // Грубый клиентский gate под право view_password — та же роль, что и для
  // reveal-пароля на вкладке аккаунтов (dep_admin / server admin / operator).
  // Финальное решение за backend'ом: без гранта `getAccount` вернёт
  // password_b64=null, и кнопка просто скажет «нет доступа».
  const canReveal =
    persona.platform_role === "dep_admin" ||
    persona.service_roles.server === "admin" ||
    persona.service_roles.server === "operator";

  return (
    <div className="p-5 flex flex-col gap-4 max-w-7xl">
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
          под выбранной сервисной учёткой. Команды сессии логируются в аудит.
        </div>
      </div>

      {accountsQ.loading && (
        <div className="text-xs text-dim">Загружаем аккаунты…</div>
      )}

      {accountsQ.error && (
        <div className="alert alert-danger flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          <div className="flex-1 text-xs">
            <div>{apiErrMsg(accountsQ.error, "Аккаунты не загрузились")}</div>
            <button
              className="btn btn-ghost mt-2"
              onClick={() => accountsQ.refetch()}
            >
              Повторить
            </button>
          </div>
        </div>
      )}

      {!accountsQ.loading && !accountsQ.error && accessible.length === 0 && (
        <div className="alert flex items-start gap-2">
          <Lock className="w-4 h-4 mt-0.5 text-dim" />
          <div className="flex-1 text-xs text-dim">
            На этом сервере нет аккаунтов, к которым у вас есть доступ.
            {accounts.length > 0 && (
              <>
                {" "}
                Всего привязано: {accounts.length}. Запросите grant у
                администратора сервиса или департамента.
              </>
            )}
          </div>
        </div>
      )}

      {accessible.length > 0 && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            Аккаунт для подключения (обязательно)
          </span>
          <select
            className="surface-2 border border-token rounded px-2 py-1"
            value={selectedAccountId}
            onChange={(e) => setSelectedAccountId(e.target.value)}
          >
            <option value="">— выберите учётку —</option>
            {accessible.map((a) => (
              <option key={a.id} value={a.id}>
                {a.login}
                {a.has_sudo ? " (sudo)" : ""}
                {a.source === "discovered" ? " · discovered" : ""}
              </option>
            ))}
          </select>
          <span className="text-dim text-xs">
            Сессия откроется под этим аккаунтом. Подготовка (Prepare) для
            консоли не требуется.
          </span>
        </label>
      )}

      {accessible.length > 0 && (
        <ConsoleSession
          serverId={serverId}
          account={selectedAccount}
          canReveal={canReveal}
        />
      )}
    </div>
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
 * открывается по «Подключить» и dispose'ится по «Отключить» / unmount.
 */
function ConsoleSession({
  serverId,
  account,
  canReveal,
}: {
  serverId: string;
  account: ServerAccount | null;
  canReveal: boolean;
}) {
  const toast = useToast();
  const mountRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const inputDisposeRef = useRef<(() => void) | null>(null);

  const [state, setState] = useState<ConnState>("idle");
  const [closeInfo, setCloseInfo] = useState<ConsoleCloseInfo | null>(null);
  const [injecting, setInjecting] = useState(false);

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

    let ws: WebSocket;
    try {
      ws = new WebSocket(
        consoleWsUrl(serverId, account.id),
        consoleWsProtocols(),
      );
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
  }, [serverId, account, state]);

  // Подставить пароль текущей учётки в терминал без перевода строки: при
  // запросе пароля (sudo и т.п.) пользователю остаётся нажать Enter. Пароль
  // достаём свежим запросом и держим в локальной переменной ровно на время
  // отправки — ни в state, ни в term.write (sudo не эхоит, не светим на экран).
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
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        {connected ? (
          <button className="btn btn-ghost" onClick={disconnect}>
            <PlugZap className="w-4 h-4" />
            Отключить
          </button>
        ) : (
          <button
            className="btn btn-primary"
            onClick={connect}
            disabled={!account}
            title={account ? "Открыть консольную сессию" : "Выберите аккаунт"}
          >
            <Plug className="w-4 h-4" />
            Подключить
          </button>
        )}
        {sessionOpen && (
          <button
            className="btn btn-ghost"
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
          </button>
        )}
        <StatusBadge state={state} />
        {account && (
          <span className="text-xs text-dim mono">
            {account.login}
            {account.has_sudo ? " (sudo)" : ""}
          </span>
        )}
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
          считает на ряд больше и низ терминала обрезается. */}
      <div
        className="border border-token rounded overflow-hidden"
        style={{ height: 480, background: "#1e1e1e", padding: 8 }}
      >
        <div ref={mountRef} style={{ height: "100%", width: "100%" }} />
      </div>
    </div>
  );
}
