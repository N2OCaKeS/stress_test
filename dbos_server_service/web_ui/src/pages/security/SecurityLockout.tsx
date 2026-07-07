import { useState } from "react";
import {
  AlertTriangle,
  Ban,
  LockOpen,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { apiErrMsg } from "@/api/client";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import {
  banUser,
  getLockoutPolicy,
  listLockedUsers,
  unbanUser,
  unlockUser,
  updateLockoutPolicy,
} from "@/api/auth/lockout";
import { listUsers, resolveUser } from "@/api/auth/users";
import { USERS as MOCK_USERS } from "@/mocks/auth";
import type { LockedUser, LockoutPolicy } from "@/api/auth/types";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useDeptLabelOpt } from "@/lib/labels";
import { formatMsk } from "@/lib/datetime";
import { isPlatformWideAdmin } from "@/lib/rbac";

const MOCK_LOCKED: LockedUser[] = [
  {
    user_id: "usr_2f1a",
    username: "ivanov",
    department_id: "dtkk",
    failed_login_attempts: 5,
    locked_until: "2026-06-23T12:40:00Z",
    is_banned: false,
  },
  {
    user_id: "usr_9c4b",
    username: "petrov",
    department_id: "core",
    failed_login_attempts: 3,
    locked_until: null,
    is_banned: false,
  },
];

/**
 * Lockout-управление (`/admin/services.security.lockout`).
 *
 * Гейт — account_admin. Экран показывает залоченные/копящие неудачные попытки
 * учётки (с разблокировкой и ручным ban/unban), ручную разблокировку по id и
 * форму политики lockout (порог + время блокировки).
 */
export function SecurityLockout() {
  const { persona } = usePersona();

  // services-блок рендерит workzone целиком (main — overflow-hidden), скролл
  // держим внутри страницы.
  const wrap = "flex-1 min-h-0 overflow-auto p-6 flex flex-col gap-4";

  if (!isPlatformWideAdmin(persona)) {
    return (
      <div className={wrap}>
        <div className="alert-danger flex items-center gap-2 text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span>
            Управление lockout доступно только <b>account_admin</b>.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className={wrap}>
      <LockedList />
      <ManualUnlock />
      <PolicyForm />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Список залоченных / failing
// ---------------------------------------------------------------------------

function LockedList() {
  const mockMode = useMockMode();
  const toast = useToast();
  const confirm = useConfirm();
  const [includeFailing, setIncludeFailing] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const listQ = useQuery<LockedUser[]>(
    () => (mockMode ? Promise.resolve(MOCK_LOCKED) : listLockedUsers(includeFailing)),
    [includeFailing, mockMode],
  );

  const rows = listQ.data ?? [];

  async function onUnlock(u: LockedUser) {
    if (mockMode) {
      toast.warn("Mock-режим — разблокировка не отправляется на backend.");
      return;
    }
    setBusyId(u.user_id);
    try {
      await unlockUser(u.user_id);
      toast.success(`Lockout снят для ${u.username}`);
      listQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  async function onBan(u: LockedUser) {
    if (mockMode) {
      toast.warn("Mock-режим — блокировка не отправляется на backend.");
      return;
    }
    if (
      !(await confirm.confirm({
        message: `Заблокировать (ban) учётку ${u.username}? Войти она не сможет до снятия бана.`,
        danger: true,
        confirmLabel: "Заблокировать",
      }))
    )
      return;
    setBusyId(u.user_id);
    try {
      await banUser(u.user_id, {
        ban_type: "permanent",
        reason: "manual ban via lockout admin",
      });
      toast.success(`${u.username} заблокирован`);
      listQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  async function onUnban(u: LockedUser) {
    if (mockMode) {
      toast.warn("Mock-режим — снятие бана не отправляется на backend.");
      return;
    }
    setBusyId(u.user_id);
    try {
      await unbanUser(u.user_id);
      toast.success(`Бан снят для ${u.username}`);
      listQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold flex items-center gap-2">
          <LockOpen className="w-4 h-4 text-accent" /> Залоченные учётки
        </h3>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-dim cursor-pointer">
            <input
              type="checkbox"
              checked={includeFailing}
              onChange={(e) => setIncludeFailing(e.target.checked)}
            />
            показывать копящие неудачные попытки
          </label>
          <button
            className="btn btn-sm flex items-center gap-1"
            onClick={() => listQ.refetch()}
            disabled={listQ.loading}
          >
            {listQ.loading ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <RefreshCcw className="w-3.5 h-3.5" />
            )}
            Обновить
          </button>
        </div>
      </div>

      {listQ.loading ? (
        <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
      ) : listQ.error ? (
        <div className="alert-danger text-xs flex items-center justify-between gap-2">
          <span>{apiErrMsg(listQ.error)}</span>
          <button className="btn btn-ghost btn-sm" onClick={() => listQ.refetch()}>
            Повторить
          </button>
        </div>
      ) : rows.length === 0 ? (
        <div className="text-xs text-dim py-4 text-center">
          Залоченных учёток нет.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {rows.map((u) => (
            <LockedRow
              key={u.user_id}
              u={u}
              busy={busyId === u.user_id}
              onUnlock={() => onUnlock(u)}
              onBan={() => onBan(u)}
              onUnban={() => onUnban(u)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function LockedRow({
  u,
  busy,
  onUnlock,
  onBan,
  onUnban,
}: {
  u: LockedUser;
  busy: boolean;
  onUnlock: () => void;
  onBan: () => void;
  onUnban: () => void;
}) {
  const deptLabel = useDeptLabelOpt(u.department_id);
  const locked = !!u.locked_until;

  return (
    <div className="cred-row flex items-center gap-3 flex-wrap">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate flex items-center gap-2">
          {u.username}
          {u.is_banned ? (
            <span className="badge danger">забанен</span>
          ) : locked ? (
            <span className="badge badge-warn">залочен</span>
          ) : (
            <span className="badge">копит попытки</span>
          )}
        </div>
        <div className="text-[11px] text-dim truncate">
          <span className="mono">{u.user_id}</span>
          {deptLabel ? ` · ${deptLabel}` : ""} · попыток:{" "}
          {u.failed_login_attempts}
          {u.locked_until ? ` · до ${formatMsk(u.locked_until)}` : ""}
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          className="btn btn-sm flex items-center gap-1"
          onClick={onUnlock}
          disabled={busy}
          title="Сбросить failed-attempts и locked_until"
        >
          {busy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <LockOpen className="w-3.5 h-3.5" />
          )}
          Разблокировать
        </button>
        {u.is_banned ? (
          <button
            className="btn btn-sm flex items-center gap-1"
            onClick={onUnban}
            disabled={busy}
          >
            <ShieldCheck className="w-3.5 h-3.5" /> Снять бан
          </button>
        ) : (
          <button
            className="btn btn-sm btn-danger flex items-center gap-1"
            onClick={onBan}
            disabled={busy}
          >
            <Ban className="w-3.5 h-3.5" /> Заблокировать
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ручная разблокировка / блокировка по выбранному пользователю
// ---------------------------------------------------------------------------

// Лёгкая форма записи пользователя для пикера: хватает id, username и email.
interface PickUser {
  id: string;
  username: string;
  email?: string | null;
}

function ManualUnlock() {
  const mockMode = useMockMode();
  const toast = useToast();
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState<"unlock" | "ban" | null>(null);

  // Список платформенных учёток для combobox'а. В live-режиме тянем первую
  // страницу с include_banned (забаненных тоже надо уметь разблокировать),
  // в mock-режиме отдаём фикстуру.
  const usersQ = useQuery<PickUser[]>(
    () =>
      mockMode
        ? Promise.resolve(
            MOCK_USERS.map((u) => ({
              id: u.id,
              username: u.username,
              email: u.email,
            })),
          )
        : listUsers({ limit: 200, include_banned: true }).then((r) =>
            r.items.map((u) => ({
              id: u.id,
              username: u.username,
              email: u.email,
            })),
          ),
    [mockMode],
  );
  const users = usersQ.data ?? [];

  async function resolveId(): Promise<string | null> {
    const name = username.trim();
    if (!name) {
      toast.warn("Выберите пользователя");
      return null;
    }
    if (mockMode) {
      toast.warn("Mock-режим — действие не отправляется на backend.");
      return null;
    }
    // Сначала ищем в загруженном списке — резолв без лишнего запроса. Если имя
    // за пределами страницы (загружено только первые 200), падаем на точечный
    // /users/resolve.
    const local = users.find(
      (u) => u.username.toLowerCase() === name.toLowerCase(),
    );
    if (local) return local.id;
    try {
      const r = await resolveUser(name);
      return r.user_id;
    } catch (e) {
      toast.error(apiErrMsg(e));
      return null;
    }
  }

  async function onUnlock() {
    setBusy("unlock");
    try {
      const id = await resolveId();
      if (!id) return;
      await unlockUser(id);
      toast.success(`Lockout снят для ${username.trim()}`);
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function onBan() {
    setBusy("ban");
    try {
      const id = await resolveId();
      if (!id) return;
      await banUser(id, {
        ban_type: "permanent",
        reason: "manual ban via lockout admin",
      });
      toast.success(`${username.trim()} заблокирован`);
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card">
      <h3 className="font-semibold flex items-center gap-2 mb-3">
        <ShieldCheck className="w-4 h-4 text-accent" /> Ручное управление
        пользователем
      </h3>
      <div className="text-xs text-dim mb-3">
        Выберите пользователя из списка (поиск по username). По выбранной учётке
        вызывается unlock или ban по её id.
      </div>
      <div className="flex items-end gap-2 flex-wrap">
        <label className="flex flex-col gap-1 text-sm flex-1 min-w-[200px]">
          <span className="text-dim text-xs">пользователь</span>
          <input
            className="input mono"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={usersQ.loading ? "загрузка…" : "ivanov"}
            list="lockout-user-options"
            autoComplete="off"
            onKeyDown={(e) => {
              if (e.key === "Enter") onUnlock();
            }}
          />
          <datalist id="lockout-user-options">
            {users.map((u) => (
              <option key={u.id} value={u.username}>
                {u.email ?? u.id}
              </option>
            ))}
          </datalist>
        </label>
        <button
          className="btn btn-primary flex items-center gap-1"
          onClick={onUnlock}
          disabled={busy !== null || !username.trim()}
        >
          {busy === "unlock" ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <LockOpen className="w-4 h-4" />
          )}
          Разблокировать
        </button>
        <button
          className="btn btn-danger flex items-center gap-1"
          onClick={onBan}
          disabled={busy !== null || !username.trim()}
        >
          {busy === "ban" ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Ban className="w-4 h-4" />
          )}
          Заблокировать
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Политика lockout
// ---------------------------------------------------------------------------

const MOCK_POLICY: LockoutPolicy = {
  max_failed_attempts: 5,
  lockout_minutes: 15,
};

function PolicyForm() {
  const mockMode = useMockMode();
  const toast = useToast();
  const [draft, setDraft] = useState<LockoutPolicy | null>(null);
  const [busy, setBusy] = useState(false);

  const policyQ = useQuery<LockoutPolicy>(
    () => (mockMode ? Promise.resolve(MOCK_POLICY) : getLockoutPolicy()),
    [mockMode],
  );

  // Подтягиваем серверную политику в редактируемый черновик при первой загрузке
  // (и после refetch, если черновик ещё не трогали).
  const policy = draft ?? policyQ.data ?? null;

  function patch(next: Partial<LockoutPolicy>) {
    setDraft((d) => ({
      ...(d ?? policyQ.data ?? MOCK_POLICY),
      ...next,
    }));
  }

  async function save() {
    if (!policy) return;
    if (mockMode) {
      toast.warn("Mock-режим — политика не отправляется на backend.");
      return;
    }
    if (policy.max_failed_attempts < 1 || policy.lockout_minutes < 1) {
      toast.warn("Порог и время блокировки должны быть ≥ 1");
      return;
    }
    setBusy(true);
    try {
      const saved = await updateLockoutPolicy({
        max_failed_attempts: policy.max_failed_attempts,
        lockout_minutes: policy.lockout_minutes,
      });
      toast.success("Политика lockout сохранена");
      setDraft(saved);
      policyQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h3 className="font-semibold flex items-center gap-2 mb-3">
        <SlidersHorizontal className="w-4 h-4 text-accent" /> Политика lockout
      </h3>
      {policyQ.loading ? (
        <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
      ) : policyQ.error ? (
        <div className="alert-danger text-xs flex items-center justify-between gap-2">
          <span>{apiErrMsg(policyQ.error)}</span>
          <button className="btn btn-ghost btn-sm" onClick={() => policyQ.refetch()}>
            Повторить
          </button>
        </div>
      ) : policy ? (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">
                max_failed_attempts (порог)
              </span>
              <input
                className="input mono"
                type="number"
                min={1}
                value={policy.max_failed_attempts}
                onChange={(e) =>
                  patch({ max_failed_attempts: Number(e.target.value) })
                }
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-dim text-xs">lockout_minutes (время)</span>
              <input
                className="input mono"
                type="number"
                min={1}
                value={policy.lockout_minutes}
                onChange={(e) =>
                  patch({ lockout_minutes: Number(e.target.value) })
                }
              />
            </label>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <button
              className="btn btn-primary flex items-center gap-1"
              onClick={save}
              disabled={busy}
            >
              {busy && <Loader2 className="w-4 h-4 animate-spin" />} Сохранить
            </button>
            <span className="text-[11px] text-dim">
              PUT /admin/lockout-policy — применяется ко всем учёткам.
            </span>
          </div>
        </>
      ) : null}
    </div>
  );
}
