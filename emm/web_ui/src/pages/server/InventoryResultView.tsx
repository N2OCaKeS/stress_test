/**
 * Вид результата инвентаризации OS-юзеров (`users.inventory` →
 * `task.result.{users, unknown_users, unlinked_existing}`).
 *
 * Полный скан бокса (`users`) показывается списком «Обнаруженные пользователи»
 * со статус-бейджем на каждого: «Не в системе» (есть в `unknown_users`), «Есть
 * аккаунт, не привязан» (`unlinked_existing`), «Системная» (управляющая учётка
 * сервера) либо «Уже в системе». Так оператор всегда видит, что нашли, даже
 * когда добавлять некого.
 *
 * Незнакомые OS-юзеры (`unknown_users`) — таблица с режимом add/ignore/skip
 * (по умолчанию skip) и батч-кнопкой «Применить»: отмеченные на add заводятся
 * как discovered-аккаунты (`importUnknownUser`), на ignore — в ignore-list
 * (`addIgnoredLogin`). Логины, под которые в отделе уже есть аккаунт, но он не
 * привязан к серверу (`unlinked_existing`), показываются отдельной секцией с
 * кнопкой «Связать» (`bindAccountServers`).
 *
 * Используется и модалкой `OsUsersDiscoveryModal` (поверх скана), и страницей
 * результата задачи `TaskResultPage` (по готовой задаче).
 */
import { useEffect, useMemo, useState } from "react";
import {
  ShieldCheck,
  UserPlus,
  EyeOff,
  Check,
  Link2,
} from "lucide-react";
import { useToast } from "@/contexts/ToastContext";
import { Dropdown } from "@/components/ui/Dropdown";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  importUnknownUser,
  addIgnoredLogin,
  bindAccountServers,
} from "@/api/server/accounts";
import type {
  InventoryUser,
  UnknownUser,
  UnlinkedExistingUser,
} from "@/api/server/types";

type RowMode = "skip" | "add" | "ignore";

/** Статус найденного логина относительно БД — для бейджа в общем списке. */
type DiscoveredStatus = "unknown" | "unlinked" | "system" | "present";

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

export function InventoryResultView({
  serverId,
  users,
  unknownUsers,
  unlinkedExisting,
  managementUser,
  onImported,
  onIgnored,
  onLinked,
  onBusyChange,
}: {
  /** Сервер, к которому относится снимок — цель импорта/привязки. */
  serverId: string | null;
  /** Полный скан бокса — все найденные OS-юзеры (`result.users`). */
  users: InventoryUser[];
  unknownUsers: UnknownUser[];
  unlinkedExisting: UnlinkedExistingUser[];
  /** Управляющая учётка сервера, если известна — помечаем «системной». */
  managementUser?: string | null;
  /** После успешного импорта хотя бы одного юзера — refetch. */
  onImported?: () => void;
  /** После добавления хотя бы одного логина в ignore-list. */
  onIgnored?: () => void;
  /** После привязки существующего аккаунта к серверу — refetch. */
  onLinked?: () => void;
  /** Поднимает наверх флаг «идёт batch-применение» (для блокировки модалки). */
  onBusyChange?: (busy: boolean) => void;
}) {
  const toast = useToast();
  const [modes, setModes] = useState<Record<string, RowMode>>({});
  const [applying, setApplying] = useState(false);
  // Логины из unlinked_existing, уже привязанные в текущем снимке — убираем из секции.
  const [linkedLogins, setLinkedLogins] = useState<Set<string>>(new Set());
  // Login, по которому сейчас крутится привязка (блокируем его кнопку).
  const [linkingLogin, setLinkingLogin] = useState<string | null>(null);

  useEffect(() => {
    onBusyChange?.(applying);
  }, [applying, onBusyChange]);

  // То, что ещё не привязали.
  const pendingUnlinked = useMemo(
    () => unlinkedExisting.filter((u) => !linkedLogins.has(u.login)),
    [unlinkedExisting, linkedLogins],
  );

  // Быстрый доступ по login для статуса в общем списке.
  const unknownByLogin = useMemo(() => {
    const m = new Map<string, UnknownUser>();
    for (const u of unknownUsers) m.set(u.login, u);
    return m;
  }, [unknownUsers]);

  const unlinkedByLogin = useMemo(() => {
    const m = new Map<string, UnlinkedExistingUser>();
    for (const u of unlinkedExisting) m.set(u.login, u);
    return m;
  }, [unlinkedExisting]);

  // Статус найденного логина: unknown → unlinked → system → present.
  // Привязку, выполненную в текущем снимке, учитываем — такой логин уже present.
  function statusOf(login: string): DiscoveredStatus {
    if (unknownByLogin.has(login)) return "unknown";
    if (unlinkedByLogin.has(login) && !linkedLogins.has(login)) return "unlinked";
    if (managementUser && login === managementUser) return "system";
    return "present";
  }

  function setMode(login: string, mode: RowMode) {
    setModes((prev) => ({ ...prev, [login]: mode }));
  }

  const selectedCount = useMemo(
    () => Object.values(modes).filter((m) => m !== "skip").length,
    [modes],
  );

  async function handleApply() {
    if (applying || !serverId) return;
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
          server_id: serverId,
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
    if (importOk > 0) onImported?.();
    if (ignoreOk > 0) onIgnored?.();

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
    if (linkingLogin || !serverId) return;
    setLinkingLogin(user.login);
    try {
      await bindAccountServers(accountId, { server_ids: [serverId] });
      toast.success(`${user.login}: аккаунт привязан к серверу`);
      setLinkedLogins((prev) => new Set(prev).add(user.login));
      onLinked?.();
    } catch (e) {
      toast.error(`${user.login}: ${linkError(e)}`);
    } finally {
      setLinkingLogin(null);
    }
  }

  // Есть ли что предложить оператору (добавить / связать).
  const nothingToAct = unknownUsers.length === 0 && pendingUnlinked.length === 0;

  return (
    <div className="flex flex-col gap-4">
      {/* Полный список обнаруженных на боксе пользователей со статусом */}
      {users.length > 0 ? (
        <div className="flex flex-col gap-2">
          <div className="text-xs uppercase text-dim">
            Обнаруженные пользователи ({users.length})
          </div>
          {nothingToAct && (
            <div className="text-xs text-dim">
              Новых пользователей для добавления нет — все обнаруженные уже
              заведены или игнорируются.
            </div>
          )}
          <div className="flex flex-col gap-1 max-h-96 overflow-y-auto">
            {users.map((u) => {
              const status = statusOf(u.login);
              if (status === "unknown") {
                const unknown = unknownByLogin.get(u.login)!;
                return (
                  <UnknownUserRow
                    key={u.login}
                    user={unknown}
                    mode={modes[u.login] ?? "skip"}
                    disabled={applying}
                    onMode={(m) => setMode(u.login, m)}
                  />
                );
              }
              if (status === "unlinked") {
                const unlinked = unlinkedByLogin.get(u.login)!;
                return (
                  <UnlinkedExistingRow
                    key={u.login}
                    user={unlinked}
                    busy={linkingLogin === u.login}
                    disabled={linkingLogin != null}
                    onLink={(accountId) => handleLink(unlinked, accountId)}
                  />
                );
              }
              return <DiscoveredUserRow key={u.login} user={u} status={status} />;
            })}
          </div>
        </div>
      ) : (
        // Старый снимок без поля users — показываем незнакомых как раньше.
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

      {/* Существующие аккаунты — связать с сервером (запасная секция, когда
          общий список не строится — нет users) */}
      {users.length === 0 && pendingUnlinked.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="text-xs uppercase text-dim">
            Существующие аккаунты — связать с сервером ({pendingUnlinked.length})
          </div>
          <div className="text-xs text-dim">
            Логины найдены на сервере, а в отделе уже есть аккаунт под них — он
            просто не привязан к этому серверу. Свяжите нужный аккаунт, чтобы его
            OS-присутствие учитывалось.
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

      {/* Применить отмеченные unknown_users */}
      {unknownUsers.length > 0 && (
        <div className="flex justify-end">
          <button
            type="button"
            className="btn btn-primary flex items-center gap-1"
            disabled={applying || selectedCount === 0 || !serverId}
            onClick={handleApply}
          >
            <Check className="w-4 h-4" />
            {applying ? "Применяем…" : `Применить (${selectedCount})`}
          </button>
        </div>
      )}
    </div>
  );
}

/** Строка обнаруженного пользователя без действий: уже в системе / системная. */
function DiscoveredUserRow({
  user,
  status,
}: {
  user: InventoryUser;
  status: "system" | "present";
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
      <span className="badge">
        {status === "system" ? "Системная" : "Уже в системе"}
      </span>
    </div>
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
          <span className="badge badge-warn">Не в системе</span>
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
        <div className="text-sm mono flex items-center gap-2">
          {user.login}
          <span className="badge">Есть аккаунт, не привязан</span>
        </div>
        <div className="text-[11px] text-dim">
          uid {user.uid}
          {single
            ? ` · аккаунт ${user.candidates[0]?.account_id} (${user.candidates[0]?.source})`
            : ` · кандидатов: ${user.candidates.length}`}
        </div>
      </div>
      {!single && (
        <Dropdown
          mode="single"
          className="min-w-[160px]"
          disabled={disabled}
          options={user.candidates.map((c) => ({ value: c.account_id, label: `${c.account_id} (${c.source})` }))}
          value={picked}
          onChange={setPicked}
        />
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
