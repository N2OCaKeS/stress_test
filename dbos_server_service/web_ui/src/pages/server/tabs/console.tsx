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
  Lock,
  Plug,
  PlugZap,
  Terminal as TerminalIcon,
} from "lucide-react";
import { listAccounts } from "@/api/server/accounts";
import {
  consoleWsProtocols,
  consoleWsUrl,
  describeConsoleClose,
  type ConsoleCloseInfo,
} from "@/api/server/console";
import { useQuery } from "@/api/auth/useQuery";
import { usePersona } from "@/contexts/PersonaContext";
import { apiErrMsg } from "@/api/client";
import { filterAccessibleAccounts } from "@/pages/server/_serverShared";
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
        <ConsoleSession serverId={serverId} account={selectedAccount} />
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
}: {
  serverId: string;
  account: ServerAccount | null;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const inputDisposeRef = useRef<(() => void) | null>(null);

  const [state, setState] = useState<ConnState>("idle");
  const [closeInfo, setCloseInfo] = useState<ConsoleCloseInfo | null>(null);

  // Монтируем терминал один раз и держим до unmount. fit на ресайз окна.
  useEffect(() => {
    if (!mountRef.current) return;
    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily:
        "'JetBrains Mono Variable', ui-monospace, SFMono-Regular, monospace",
      fontSize: 13,
      theme: { background: "#1e1e1e" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(mountRef.current);
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

    return () => {
      window.removeEventListener("resize", onResize);
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

  const connected = state === "open" || state === "connecting";

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

      <div
        ref={mountRef}
        className="border border-token rounded"
        style={{ height: 480, background: "#1e1e1e", padding: 8 }}
      />
    </div>
  );
}
