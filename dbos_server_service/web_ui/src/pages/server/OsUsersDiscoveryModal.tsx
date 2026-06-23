/**
 * Поиск OS-юзеров на сервере (discovery) — модалка над страницей
 * `Server.Пользователи`.
 *
 * Сверху выбор сервера отдела + «Сканировать»: дёргает `usersInventory`,
 * поллит задачу (паттерн `useTaskOutcome`), из `result.unknown_users` собирает
 * таблицу незнакомых OS-юзеров. У каждой строки режим add/ignore/skip
 * (по умолчанию skip). «Применить» батчем заводит отмеченные на add как
 * discovered-аккаунты (`importUnknownUser`), а отмеченные на ignore — в
 * ignore-list (`addIgnoredLogin`), показывает сводку тостом.
 *
 * Отдельная мини-секция «Проверка логина» переиспользует тот же снимок: по
 * введённому логину показывает вердикт (незнакомый / расхождение / совпадает /
 * не найден). Отдельного backend-роута нет — фильтруем результат скана.
 */
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ScanSearch,
  Server as ServerIcon,
  ShieldCheck,
  RotateCw,
  UserPlus,
  EyeOff,
  AlertCircle,
  Check,
  Search,
  Link2,
} from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  importUnknownUser,
  addIgnoredLogin,
  bindAccountServers,
} from "@/api/server/accounts";
import { usersInventory } from "@/api/server/misc";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import type {
  Server,
  UnknownUser,
  UnlinkedExistingUser,
  RevisionAccountDiff,
} from "@/api/server/types";

type RowMode = "skip" | "add" | "ignore";

function importError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "нет прав на импорт";
    if (e.status === 404) return "сервер не найден";
    if (e.status === 409) return "login уже заведён";
  }
  return apiErrMsg(e, "ошибка импорта");
}

function ignoreError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "нет прав на ignore-list";
    if (e.status === 409) return "уже в ignore-list";
  }
  return apiErrMsg(e, "ошибка ignore-list");
}

function linkError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "нет прав на привязку";
    if (e.status === 404) return "аккаунт или сервер не найдены";
    if (e.status === 409) return "login занят на сервере другим аккаунтом";
  }
  return apiErrMsg(e, "ошибка привязки");
}

export function OsUsersDiscoveryModal({
  servers,
  serverName,
  onClose,
  onImported,
  onIgnored,
  onLinked,
}: {
  servers: Server[];
  serverName: (id: string) => string;
  onClose: () => void;
  /** После успешного импорта хотя бы одного юзера — refetch списка аккаунтов. */
  onImported: () => void;
  /** После добавления хотя бы одного логина в ignore-list. */
  onIgnored: () => void;
  /** После привязки существующего аккаунта к серверу — refetch списка аккаунтов. */
  onLinked?: () => void;
}) {
  const toast = useToast();
  const scan = useTaskOutcome();
  const [serverId, setServerId] = useState<string>(servers[0]?.id ?? "");
  // Сервер, по которому крутится/завершён текущий скан.
  const [scannedServer, setScannedServer] = useState<string | null>(null);
  const [modes, setModes] = useState<Record<string, RowMode>>({});
  const [applying, setApplying] = useState(false);
  const [checkLogin, setCheckLogin] = useState("");
  // Логины из unlinked_existing, уже привязанные в текущем скане — убираем из секции.
  const [linkedLogins, setLinkedLogins] = useState<Set<string>>(new Set());
  // Login, по которому сейчас крутится привязка (блокируем его кнопку).
  const [linkingLogin, setLinkingLogin] = useState<string | null>(null);

  const scanResult = scan.tracked?.result;

  const unknownUsers = useMemo<UnknownUser[]>(() => {
    if (!scanResult || typeof scanResult !== "object") return [];
    const raw = (scanResult as { unknown_users?: unknown }).unknown_users;
    return Array.isArray(raw) ? (raw as UnknownUser[]) : [];
  }, [scanResult]);

  const diffs = useMemo<RevisionAccountDiff[]>(() => {
    if (!scanResult || typeof scanResult !== "object") return [];
    const raw = (scanResult as { diffs?: unknown }).diffs;
    return Array.isArray(raw) ? (raw as RevisionAccountDiff[]) : [];
  }, [scanResult]);

  const unlinkedExisting = useMemo<UnlinkedExistingUser[]>(() => {
    if (!scanResult || typeof scanResult !== "object") return [];
    const raw = (scanResult as { unlinked_existing?: unknown }).unlinked_existing;
    return Array.isArray(raw) ? (raw as UnlinkedExistingUser[]) : [];
  }, [scanResult]);

  // То, что ещё не привязали в текущем скане.
  const pendingUnlinked = useMemo(
    () => unlinkedExisting.filter((u) => !linkedLogins.has(u.login)),
    [unlinkedExisting, linkedLogins],
  );

  const polling = scan.tracked?.polling ?? false;
  const scanDone =
    scan.tracked != null &&
    !scan.tracked.polling &&
    scan.tracked.status === "succeeded";
  const scanFailed =
    scan.tracked != null &&
    !scan.tracked.polling &&
    (scan.tracked.status === "failed" || scan.tracked.error != null);

  // Новый скан — режимы строк и привязанные логины сбрасываем (могли смениться).
  useEffect(() => {
    setModes({});
    setLinkedLogins(new Set());
  }, [scan.tracked?.taskId]);

  function setMode(login: string, mode: RowMode) {
    setModes((prev) => ({ ...prev, [login]: mode }));
  }

  async function handleScan() {
    if (!serverId || polling) return;
    scan.reset();
    setScannedServer(serverId);
    try {
      const res = await usersInventory(serverId);
      scan.track("discovery", res.task_id, res.status);
    } catch (e) {
      setScannedServer(null);
      toast.error(apiErrMsg(e, "Не удалось запустить скан"));
    }
  }

  async function handleApply() {
    if (applying || !scannedServer) return;
    const toAdd = unknownUsers.filter((u) => modes[u.login] === "add");
    const toIgnore = unknownUsers.filter((u) => modes[u.login] === "ignore");
    if (toAdd.length === 0 && toIgnore.length === 0) {
      toast.error("Отметьте хотя бы одного пользователя.");
      return;
    }
    setApplying(true);
    const importResults = await Promise.allSettled(
      toAdd.map((u) =>
        importUnknownUser({
          server_id: scannedServer,
          login: u.login,
          has_sudo: u.has_sudo,
          unix_groups: u.unix_groups,
          shell: u.shell,
          source: "discovered",
        }),
      ),
    );
    const ignoreResults = await Promise.allSettled(
      toIgnore.map((u) => addIgnoredLogin({ login: u.login })),
    );
    setApplying(false);

    const importOk = importResults.filter((r) => r.status === "fulfilled").length;
    const ignoreOk = ignoreResults.filter((r) => r.status === "fulfilled").length;
    const failures: string[] = [];
    importResults.forEach((r, i) => {
      if (r.status === "rejected") {
        failures.push(`${toAdd[i].login}: ${importError(r.reason)}`);
      }
    });
    ignoreResults.forEach((r, i) => {
      if (r.status === "rejected") {
        failures.push(`${toIgnore[i].login}: ${ignoreError(r.reason)}`);
      }
    });

    const okParts: string[] = [];
    if (importOk > 0) okParts.push(`импортировано: ${importOk}`);
    if (ignoreOk > 0) okParts.push(`в ignore: ${ignoreOk}`);
    if (okParts.length > 0) toast.success(okParts.join(", "));
    if (failures.length > 0) {
      toast.error(`Ошибки: ${failures.join("; ")}`);
    }
    if (importOk > 0) onImported();
    if (ignoreOk > 0) onIgnored();

    // Применённые строки убираем из выбора, чтобы не дёргать повторно.
    const applied = new Set([
      ...toAdd.map((u) => u.login),
      ...toIgnore.map((u) => u.login),
    ]);
    setModes((prev) => {
      const next = { ...prev };
      for (const login of applied) {
        // оставляем только проваленные на повторное применение
        const failedLogin = failures.some((f) => f.startsWith(`${login}: `));
        if (!failedLogin) next[login] = "skip";
      }
      return next;
    });
  }

  async function handleLink(user: UnlinkedExistingUser, accountId: string) {
    if (linkingLogin || !scannedServer) return;
    setLinkingLogin(user.login);
    try {
      await bindAccountServers(accountId, { server_ids: [scannedServer] });
      toast.success(`${user.login}: аккаунт привязан к серверу`);
      setLinkedLogins((prev) => new Set(prev).add(user.login));
      onLinked?.();
    } catch (e) {
      toast.error(`${user.login}: ${linkError(e)}`);
    } finally {
      setLinkingLogin(null);
    }
  }

  // Вердикт по введённому логину относительно последнего скана.
  const checkVerdict = useMemo(() => {
    const term = checkLogin.trim();
    if (!term || !scanDone) return null;
    const unknown = unknownUsers.find((u) => u.login === term);
    if (unknown) {
      return {
        kind: "warn" as const,
        text: `Незнакомый OS-юзер (uid ${unknown.uid}${unknown.has_sudo ? ", sudo" : ""}) — не привязан к аккаунту.`,
      };
    }
    const diff = diffs.find((d) => d.login === term);
    if (diff) {
      return {
        kind: "warn" as const,
        text: "Привязан, но есть расхождение атрибутов с БД (см. Ревизию).",
      };
    }
    return {
      kind: "ok" as const,
      text: "Не найден среди незнакомых и расхождений: привязан и совпадает либо отсутствует на боксе.",
    };
  }, [checkLogin, scanDone, unknownUsers, diffs]);

  const selectedCount = useMemo(
    () => Object.values(modes).filter((m) => m !== "skip").length,
    [modes],
  );

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !applying && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          style={{ maxWidth: 720 }}
          onInteractOutside={(e) => applying && e.preventDefault()}
          onEscapeKeyDown={(e) => applying && e.preventDefault()}
        >
          <div className="modal-header">
            <ScanSearch className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Поиск пользователей на ОС
            </Dialog.Title>
          </div>

          <div className="modal-body flex flex-col gap-4">
            <Dialog.Description className="text-xs text-dim">
              Сканирует OS-юзеров сервера (users/inventory) и показывает тех, кто
              не привязан к аккаунту и не в ignore-list. Каждого можно завести в
              БД (discovered) или отправить в ignore-list.
            </Dialog.Description>

            {/* Выбор сервера + скан */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-dim flex items-center gap-1">
                <ServerIcon className="w-3.5 h-3.5" /> Сервер:
              </span>
              <select
                className="surface-2 border border-token rounded px-2 py-1 text-sm flex-1 min-w-[180px]"
                value={serverId}
                disabled={polling}
                onChange={(e) => setServerId(e.target.value)}
              >
                {servers.length === 0 && (
                  <option value="">— нет серверов —</option>
                )}
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.display_name || s.hostname}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-sm btn-primary flex items-center gap-1"
                disabled={!serverId || polling}
                onClick={handleScan}
              >
                <ScanSearch
                  className={`w-3.5 h-3.5 ${polling ? "animate-spin" : ""}`}
                />
                {polling ? "Сканируем…" : "Сканировать"}
              </button>
            </div>

            {polling && (
              <div className="alert text-sm flex items-center gap-2" role="status">
                <RotateCw className="w-4 h-4 animate-spin shrink-0" />
                <span>
                  Скан {scannedServer ? `(${serverName(scannedServer)}) ` : ""}
                  запущен — ждём результат…
                </span>
              </div>
            )}

            {scanFailed && (
              <div className="alert-danger text-sm flex items-start gap-2">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                <span>
                  Скан не удался: {scan.tracked?.error ?? "задача завершилась ошибкой"}
                </span>
              </div>
            )}

            {/* Таблица незнакомых юзеров */}
            {scanDone && (
              <div className="flex flex-col gap-2">
                {unknownUsers.length === 0 ? (
                  <div className="text-sm text-dim text-center py-4">
                    Новых пользователей не найдено.
                  </div>
                ) : (
                  <>
                    <div className="text-xs uppercase text-dim">
                      Незнакомые пользователи ({unknownUsers.length})
                    </div>
                    <div className="flex flex-col gap-1 max-h-72 overflow-y-auto">
                      {unknownUsers.map((u) => (
                        <UnknownUserRow
                          key={u.login}
                          user={u}
                          mode={modes[u.login] ?? "skip"}
                          disabled={applying}
                          onMode={(m) => setMode(u.login, m)}
                        />
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Существующие аккаунты — связать с сервером */}
            {scanDone && pendingUnlinked.length > 0 && (
              <div className="flex flex-col gap-2">
                <div className="text-xs uppercase text-dim">
                  Существующие аккаунты — связать с сервером (
                  {pendingUnlinked.length})
                </div>
                <div className="text-xs text-dim">
                  Логины найдены на сервере, а в отделе уже есть аккаунт под них —
                  он просто не привязан к этому серверу. Свяжите нужный аккаунт,
                  чтобы его OS-присутствие учитывалось.
                </div>
                <div className="flex flex-col gap-1 max-h-72 overflow-y-auto">
                  {pendingUnlinked.map((u) => (
                    <UnlinkedExistingRow
                      key={u.login}
                      user={u}
                      busy={linkingLogin === u.login}
                      disabled={linkingLogin != null}
                      onLink={(accountId) => handleLink(u, accountId)}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Проверка конкретного логина */}
            <div className="card flex flex-col gap-2">
              <div className="text-xs uppercase text-dim flex items-center gap-2">
                <Search className="w-3 h-3" /> Проверка логина
              </div>
              <div className="text-xs text-dim">
                По данным последнего скана. Сначала просканируйте сервер.
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  className="field-input mono flex-1 min-w-[160px]"
                  value={checkLogin}
                  onChange={(e) => setCheckLogin(e.target.value)}
                  placeholder="login для проверки"
                  aria-label="login для проверки"
                />
              </div>
              {checkLogin.trim() && !scanDone && (
                <div className="text-xs text-dim">
                  Нет данных скана — нажмите «Сканировать».
                </div>
              )}
              {checkVerdict && (
                <div
                  className={`text-sm ${checkVerdict.kind === "ok" ? "text-dim" : "alert-warn"}`}
                >
                  {checkVerdict.text}
                </div>
              )}
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={applying}>
              Закрыть
            </button>
            <button
              type="button"
              className="btn btn-primary flex items-center gap-1"
              disabled={applying || selectedCount === 0}
              onClick={handleApply}
            >
              <Check className="w-4 h-4" />
              {applying ? "Применяем…" : `Применить (${selectedCount})`}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function UnknownUserRow({
  user,
  mode,
  disabled,
  onMode,
}: {
  user: UnknownUser;
  mode: RowMode;
  disabled: boolean;
  onMode: (mode: RowMode) => void;
}) {
  return (
    <div className="border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap">
      <div className="flex-1 min-w-[160px]">
        <div className="text-sm mono flex items-center gap-2">
          {user.login}
          {user.has_sudo && (
            <span className="badge badge-warn flex items-center gap-1">
              <ShieldCheck className="w-3 h-3" /> sudo
            </span>
          )}
        </div>
        <div className="text-[11px] text-dim">
          uid {user.uid}
          {user.shell ? ` · ${user.shell}` : ""}
          {user.unix_groups.length > 0
            ? ` · ${user.unix_groups.join(", ")}`
            : ""}
        </div>
      </div>
      <label className="inline-flex items-center gap-1 text-xs">
        <input
          type="radio"
          name={`mode-${user.login}`}
          aria-label={`Добавить ${user.login}`}
          checked={mode === "add"}
          disabled={disabled}
          onChange={() => onMode("add")}
        />
        <UserPlus className="w-3.5 h-3.5" /> Добавить
      </label>
      <label className="inline-flex items-center gap-1 text-xs">
        <input
          type="radio"
          name={`mode-${user.login}`}
          aria-label={`Игнорировать ${user.login}`}
          checked={mode === "ignore"}
          disabled={disabled}
          onChange={() => onMode("ignore")}
        />
        <EyeOff className="w-3.5 h-3.5" /> Игнорировать
      </label>
      <label className="inline-flex items-center gap-1 text-xs text-dim">
        <input
          type="radio"
          name={`mode-${user.login}`}
          aria-label={`Пропустить ${user.login}`}
          checked={mode === "skip"}
          disabled={disabled}
          onChange={() => onMode("skip")}
        />
        Пропустить
      </label>
    </div>
  );
}

function UnlinkedExistingRow({
  user,
  busy,
  disabled,
  onLink,
}: {
  user: UnlinkedExistingUser;
  busy: boolean;
  disabled: boolean;
  onLink: (accountId: string) => void;
}) {
  const single = user.candidates.length === 1;
  const [picked, setPicked] = useState<string>(
    user.candidates[0]?.account_id ?? "",
  );
  const target = single ? user.candidates[0]?.account_id ?? "" : picked;

  return (
    <div className="border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap">
      <div className="flex-1 min-w-[160px]">
        <div className="text-sm mono">{user.login}</div>
        <div className="text-[11px] text-dim">
          uid {user.uid}
          {single
            ? ` · аккаунт ${user.candidates[0]?.account_id} (${user.candidates[0]?.source})`
            : ` · кандидатов: ${user.candidates.length}`}
        </div>
      </div>
      {!single && (
        <select
          className="surface-2 border border-token rounded px-2 py-1 text-xs min-w-[160px]"
          value={picked}
          disabled={disabled}
          aria-label={`Аккаунт для ${user.login}`}
          onChange={(e) => setPicked(e.target.value)}
        >
          {user.candidates.map((c) => (
            <option key={c.account_id} value={c.account_id}>
              {c.account_id} ({c.source})
            </option>
          ))}
        </select>
      )}
      <button
        type="button"
        className="btn btn-sm btn-primary flex items-center gap-1"
        disabled={disabled || !target}
        aria-label={`Связать ${user.login}`}
        onClick={() => target && onLink(target)}
      >
        <Link2 className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`} />
        {busy ? "Связываем…" : "Связать"}
      </button>
    </div>
  );
}
