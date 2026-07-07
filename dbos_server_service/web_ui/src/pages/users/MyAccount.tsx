import { useMemo, useState } from "react";
import {
  KeyRound,
  Mail,
  Monitor,
  LogOut,
  ShieldCheck,
  User as UserIcon,
  UsersRound,
  Lock,
  Copy,
  Plus,
  Trash2,
} from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { CertHelpModal } from "@/components/CertHelpModal";
import { Tabs } from "@/components/ui/Tabs";
import {
  changeMyPassword,
  getUserGroups,
  getUserPermissions,
  listMySessions,
  patchMe,
  revokeAllMySessions,
  revokeMySession,
} from "@/api/auth/users";
import {
  createToken,
  listMyTokens,
  revokeToken,
} from "@/api/auth/tokens";
import { useMockMode, useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { useAuth } from "@/contexts/AuthContext";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useDeptLabel, useServiceLabel } from "@/lib/labels";
import { PASSWORD_POLICY_MESSAGE } from "@/lib/passwordPolicy";
import { formatMskShort, mskDateOffset } from "@/lib/datetime";
import type {
  Group,
  PATCreateResponse,
  PersonalAccessToken,
  ServiceName,
  SessionListResponse,
  UserPermissionsResponse,
} from "@/api/auth/types";

/**
 * Self-service `/me` console.
 *
 * Shows everything a user can do with their own account without invoking
 * admin endpoints: profile (read-only, backend has no PATCH /me), password
 * change, personal access tokens, active sessions, group memberships and the
 * derived permissions matrix.
 */
export function MyAccount() {
  const { user } = useAuth();
  const mockMode = useMockMode();
  const [tab, setTab] = useState<TabId>("profile");
  // Bumped when something outside SessionsCard (e.g. ProfileCard's "logout
  // others" button) invalidates the sessions list — SessionsCard re-mounts
  // and its useQuery refetches.
  const [sessionsRefreshKey, setSessionsRefreshKey] = useState(0);
  const bumpSessions = () => setSessionsRefreshKey((k) => k + 1);
  // Модалку установки CA-сертификата открывают и с экрана логина, и отсюда —
  // залогиненному юзеру она нужна, чтобы поставить сертификат на другом
  // устройстве или переустановить после переустановки браузера/ОС.
  const [certHelpOpen, setCertHelpOpen] = useState(false);

  return (
    <Shell breadcrumb="Главная / Личные настройки">
      <main className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="px-8 pt-6 pb-2 w-full flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-2xl font-bold mb-1">
              {user?.username ?? "— (не авторизован)"}
            </div>
            <div className="text-dim text-sm">
              {user?.platform_role ?? "user"} ·{" "}
              <HeaderDeptLabel
                deptName={user?.department_name}
                deptId={user?.department_id}
              />
            </div>
          </div>
          <button
            type="button"
            className="btn flex items-center gap-1.5 shrink-0"
            onClick={() => setCertHelpOpen(true)}
          >
            <ShieldCheck className="w-4 h-4" /> Установить сертификат
          </button>
        </div>

        <div className="w-full px-8">
          <Tabs
            active={tab}
            onChange={(id) => setTab(id as TabId)}
            tabs={[
              { id: "profile", label: "Профиль", icon: <UserIcon className="w-3 h-3" /> },
              { id: "password", label: "Пароль", icon: <KeyRound className="w-3 h-3" /> },
              { id: "tokens", label: "PAT", icon: <Lock className="w-3 h-3" /> },
              { id: "sessions", label: "Сессии", icon: <Monitor className="w-3 h-3" /> },
              { id: "groups", label: "Группы", icon: <UsersRound className="w-3 h-3" /> },
              { id: "perms", label: "Разрешения", icon: <ShieldCheck className="w-3 h-3" /> },
            ]}
          />
        </div>

        <div className="scroll-block w-full px-8 py-6 flex flex-col gap-5">
          {tab === "profile" && (
            <ProfileCard mockMode={mockMode} onSessionsInvalidated={bumpSessions} />
          )}
          {tab === "password" && <PasswordCard mockMode={mockMode} />}
          {tab === "tokens" && <TokensCard mockMode={mockMode} />}
          {tab === "sessions" && (
            <SessionsCard key={sessionsRefreshKey} mockMode={mockMode} />
          )}
          {tab === "groups" && <GroupsCard mockMode={mockMode} />}
          {tab === "perms" && <PermissionsCard mockMode={mockMode} />}
        </div>
      </main>

      <CertHelpModal
        open={certHelpOpen}
        onClose={() => setCertHelpOpen(false)}
      />
    </Shell>
  );
}

type TabId = "profile" | "password" | "tokens" | "sessions" | "groups" | "perms";

// ---------------------------------------------------------------------------
// Profile (read-only — backend has no PATCH /me)
// ---------------------------------------------------------------------------

function ProfileCard({
  mockMode,
  onSessionsInvalidated,
}: {
  mockMode: boolean;
  onSessionsInvalidated?: () => void;
}) {
  const { user, reload: reloadIdentity } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  // PATCH /me — display_name + email. Поля инициализируем из текущего
  // identity, кнопка «Сохранить» активна только если что-то изменилось.
  // Backend пускает любого юзера без отдельной admin-роли — это self-service.
  const [displayName, setDisplayName] = useState<string>(user?.display_name ?? "");
  const [email, setEmail] = useState<string>(user?.email ?? "");
  const [saving, setSaving] = useState(false);

  const initialDisplayName = user?.display_name ?? "";
  const initialEmail = user?.email ?? "";
  const dirty =
    displayName !== initialDisplayName || email !== initialEmail;

  async function saveProfile() {
    if (mockMode) {
      toast.info("mock: PATCH /me");
      return;
    }
    if (!dirty) return;
    setSaving(true);
    try {
      // Только реально изменённые поля — иначе backend audit пишет noop.
      const body: { display_name?: string | null; email?: string | null } = {};
      if (displayName !== initialDisplayName) {
        body.display_name = displayName.trim() ? displayName.trim() : null;
      }
      if (email !== initialEmail) {
        body.email = email.trim() ? email.trim() : null;
      }
      await patchMe(body);
      toast.success("Профиль обновлён");
      // reload подтягивает свежий /me в AuthContext — шапка/сайдбар
      // получают новый display_name без перезагрузки страницы.
      await reloadIdentity?.();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось обновить профиль"));
    } finally {
      setSaving(false);
    }
  }

  async function revokeOthers() {
    if (mockMode) {
      toast.info("mock: revoke all (except current)");
      return;
    }
    setBusy(true);
    try {
      const r = await revokeAllMySessions(true);
      toast.success(`Отозвано сессий: ${r.revoked_count}`);
      // Если SessionsCard открыт в соседней вкладке — его список устарел,
      // просим родителя пере-смонтировать карточку и обновить useQuery.
      onSessionsInvalidated?.();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось отозвать сессии"));
    } finally {
      setBusy(false);
    }
  }

  const services = user?.allowed_services ?? [];
  const roles = user?.service_roles ?? {};

  return (
    <div className="card">
      <div className="font-semibold flex items-center gap-2 mb-3">
        <Mail className="w-4 h-4 text-accent" /> Профиль
      </div>
      <div className="text-xs text-dim mb-4">
        display_name и email можно отредактировать самостоятельно.
        Department, platform_role и username меняет администратор.
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">display_name</span>
          <input
            className="input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="ФИО или ник для UI"
            maxLength={256}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">email</span>
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
          />
        </label>
      </div>
      <div className="flex justify-end mb-4">
        <button
          className="btn btn-primary"
          onClick={saveProfile}
          disabled={!dirty || saving}
        >
          {saving ? "..." : "Сохранить профиль"}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
        <StatRow k="username" v={<span className="mono">{user?.username ?? "—"}</span>} />
        <StatRow k="display_name" v={<span>{user?.display_name ?? "—"}</span>} />
        <StatRow k="user_id" v={<span className="mono">{user?.user_id ?? "—"}</span>} />
        <StatRow
          k="department"
          v={
            <HeaderDeptLabel
              deptName={user?.department_name}
              deptId={user?.department_id}
            />
          }
        />
        <StatRow k="platform_role" v={<span className="mono">{user?.platform_role ?? "—"}</span>} />
        <StatRow
          k="status"
          v={
            // `/me` отдаёт только is_banned — поля status/is_active в
            // IdentityContext нет, поэтому состояние выводим из бан-флага.
            <span className={`badge badge-${user?.is_banned ? "danger" : "ok"}`}>
              {user?.is_banned ? "banned" : "active"}
            </span>
          }
        />
        <StatRow
          k="is_active"
          v={!user?.is_banned ? <span className="text-ok">да</span> : <span className="text-warn">нет</span>}
        />
      </div>

      <div className="mt-4">
        <div className="text-xs text-dim mb-1">allowed_services</div>
        {services.length === 0 ? (
          <span className="text-xs text-dim italic">— нет доступных сервисов</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {services.map((s) => (
              <span key={s} className="badge">
                {s}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="mt-4">
        <div className="text-xs text-dim mb-1">service_roles</div>
        {Object.keys(roles).length === 0 ? (
          <span className="text-xs text-dim italic">— ролей нет</span>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(roles).map(([svc, rs]) => (
                <tr key={svc} className="border-t border-token">
                  <td className="py-1 pr-3 text-xs">
                    <ServiceInline name={svc} />
                  </td>
                  <td className="py-1 text-xs">
                    {(rs ?? []).length === 0 ? (
                      <span className="text-dim italic">—</span>
                    ) : (
                      (rs ?? []).join(", ")
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="mt-5 flex gap-2">
        <button
          className="btn btn-danger flex items-center gap-1"
          onClick={revokeOthers}
          disabled={busy}
        >
          <LogOut className="w-4 h-4" /> Выйти из всех сессий, кроме текущей
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Password
// ---------------------------------------------------------------------------

function PasswordCard({ mockMode }: { mockMode: boolean }) {
  const toast = useToast();
  const auth = useAuth();
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  const mismatch = newPwd.length > 0 && confirm.length > 0 && newPwd !== confirm;
  const tooShort = newPwd.length > 0 && newPwd.length < 12;
  const noLetter = newPwd.length > 0 && !/[A-Za-zА-Яа-яЁё]/.test(newPwd);
  const noDigit = newPwd.length > 0 && !/\d/.test(newPwd);
  const policyBad = tooShort || noLetter || noDigit;

  async function submit() {
    if (mismatch) {
      toast.warn("Пароли не совпадают");
      return;
    }
    if (policyBad) {
      toast.warn(PASSWORD_POLICY_MESSAGE);
      return;
    }
    if (mockMode) {
      toast.success("mock: пароль изменён");
      setOldPwd("");
      setNewPwd("");
      setConfirm("");
      return;
    }
    setBusy(true);
    try {
      await changeMyPassword({ old_password: oldPwd, new_password: newPwd });
      // POST /users/me/password ревочит ВСЕ сессии, включая текущую — без
      // немедленного re-login следующий запрос получит 401 и юзера выкинет
      // на /login. Тот же приём применяется в ForcePasswordChangeModal.
      const username = auth.user?.username;
      if (!username) {
        throw new Error("Не удалось определить username для re-login");
      }
      await auth.login({ username, password: newPwd });
      toast.success("Пароль изменён");
      setOldPwd("");
      setNewPwd("");
      setConfirm("");
    } catch (e) {
      toast.error(apiErrMsg(e, String(e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card w-full">
      <div className="font-semibold flex items-center gap-2 mb-3">
        <KeyRound className="w-4 h-4 text-accent" /> Смена пароля
      </div>
      <input
        type="password"
        className="input mb-2"
        placeholder="Текущий пароль"
        value={oldPwd}
        onChange={(e) => setOldPwd(e.target.value)}
        autoComplete="current-password"
      />
      <input
        type="password"
        className="input mb-1"
        placeholder="Новый пароль"
        value={newPwd}
        onChange={(e) => setNewPwd(e.target.value)}
        autoComplete="new-password"
      />
      <div className="text-[11px] text-dim mb-2">{PASSWORD_POLICY_MESSAGE}</div>
      <input
        type="password"
        className="input mb-2"
        placeholder="Повторите новый пароль"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
        autoComplete="new-password"
      />
      {tooShort && <div className="text-xs text-warn mb-2">Минимум 12 символов</div>}
      {!tooShort && noLetter && <div className="text-xs text-warn mb-2">Нужна минимум одна буква</div>}
      {!tooShort && !noLetter && noDigit && <div className="text-xs text-warn mb-2">Нужна минимум одна цифра</div>}
      {mismatch && <div className="text-xs text-warn mb-2">Пароли не совпадают</div>}
      <button
        className="btn btn-primary w-full"
        onClick={submit}
        disabled={busy || !oldPwd || !newPwd || mismatch || policyBad}
      >
        {busy ? "..." : "Применить"}
      </button>
      <div className="text-[11px] text-dim mt-2">
        После смены все активные сессии будут отозваны. Текущая сессия будет
        автоматически переподнята с новым паролем — выходить и заходить заново
        не нужно.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PAT
// ---------------------------------------------------------------------------

const KNOWN_SERVICES: ServiceName[] = [
  "auth_service",
  "secret_service",
  "server_service",
  "loging_service",
  "config_service",
  "docker_registry",
];

function TokensCard({ mockMode }: { mockMode: boolean }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const q = useQuery<PersonalAccessToken[]>(
    () => listMyTokens(),
    [],
    { enabled: !mockMode },
  );
  const [creating, setCreating] = useState(false);
  const [oneShot, setOneShot] = useState<PATCreateResponse | null>(null);

  async function onRevoke(t: PersonalAccessToken) {
    if (mockMode) {
      toast.info(`mock: revoke ${t.name}`);
      return;
    }
    if (
      !(await confirm({
        message: `Отозвать PAT «${t.name}»?`,
        confirmLabel: "Отозвать",
        danger: true,
      }))
    )
      return;
    try {
      await revokeToken(t.token_id);
      toast.success("Токен отозван");
      // Если только что отозвали тот самый токен, чей plaintext ещё висит
      // в one-shot панели — убираем панель, иначе показываем «живой» секрет
      // уже мёртвого токена.
      if (oneShot?.token_id === t.token_id) setOneShot(null);
      q.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось отозвать токен"));
    }
  }

  const items = useMemo(() => {
    const now = Date.now();
    return (q.data ?? []).filter(
      (t) =>
        !t.revoked_at &&
        (!t.expires_at || new Date(t.expires_at).getTime() > now),
    );
  }, [q.data]);

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <div className="font-semibold flex items-center gap-2">
            <Lock className="w-4 h-4 text-accent" /> Personal Access Tokens
          </div>
          <button
            className="btn btn-primary flex items-center gap-1"
            onClick={() => setCreating(true)}
            disabled={creating}
          >
            <Plus className="w-4 h-4" /> Создать PAT
          </button>
        </div>
        <div className="text-xs text-dim mb-3">
          PAT привязаны к вашему пользователю. Plaintext-токен показывается один
          раз при создании — сохраните его сразу.
        </div>

        {oneShot && (
          <OneShotPanel resp={oneShot} onClose={() => setOneShot(null)} />
        )}
        {creating && (
          <CreatePatForm
            mockMode={mockMode}
            onCancel={() => setCreating(false)}
            onCreated={(resp) => {
              setCreating(false);
              setOneShot(resp);
              q.refetch();
            }}
          />
        )}

        {!mockMode && q.loading && (
          <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
        )}
        {!mockMode && q.error && (
          <div className="alert-danger">{q.error.message}</div>
        )}
        {mockMode && (
          <div className="text-xs text-dim italic">
            mock-режим: список PAT не подгружается.
          </div>
        )}
        {!mockMode && !q.loading && !q.error && items.length === 0 && (
          <div className="empty-card">Токенов нет.</div>
        )}
        {items.length > 0 && (
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">имя</th>
                <th className="pb-2 pr-3">области</th>
                <th className="pb-2 pr-3">создан</th>
                <th className="pb-2 pr-3">истекает</th>
                <th className="pb-2 pr-3">посл. использование</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.token_id} className="border-t border-token">
                  <td className="py-2 pr-3">
                    <div className="font-medium">{t.name}</div>
                    <div className="text-[11px] text-dim mono">{t.token_id}</div>
                  </td>
                  <td className="text-xs">
                    {t.allowed_services.join(", ")}
                  </td>
                  <td className="text-xs text-dim mono">{fmtTs(t.created_at)}</td>
                  <td className="text-xs text-dim mono">{fmtTs(t.expires_at)}</td>
                  <td className="text-xs text-dim mono">{fmtTs(t.last_used_at ?? null)}</td>
                  <td className="text-right">
                    <button
                      className="btn btn-danger btn-sm flex items-center gap-1"
                      onClick={() => onRevoke(t)}
                    >
                      <Trash2 className="w-3 h-3" /> отозвать
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function CreatePatForm({
  mockMode,
  onCancel,
  onCreated,
}: {
  mockMode: boolean;
  onCancel: () => void;
  onCreated: (r: PATCreateResponse) => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  const availableServices = resolveAvailableServices(user);
  const [name, setName] = useState("");
  // expires теперь обязателен и ограничен 6 месяцами. Дефолт = 90 дней —
  // sane middle между «один раз настроил» и «не успел протухнуть до ротации».
  // min = завтра (today выпавший в полночь backend'а уже истёк), max = +180 дней.
  const expBounds = patExpiresBounds();
  const [expires, setExpires] = useState(expBounds.default);
  const [scopes, setScopes] = useState<string[]>(availableServices);
  const [pending, setPending] = useState(false);

  const toggle = (svc: string) =>
    setScopes((prev) =>
      prev.includes(svc) ? prev.filter((s) => s !== svc) : [...prev, svc],
    );

  const noScopes = scopes.length === 0;
  const noServicesAvailable = availableServices.length === 0;
  const expiresInvalid =
    !expires || expires < expBounds.min || expires > expBounds.max;

  async function submit() {
    if (!name.trim()) {
      toast.warn("Укажите имя токена");
      return;
    }
    if (noScopes) {
      toast.warn("Выберите минимум 1 сервис");
      return;
    }
    if (expiresInvalid) {
      toast.warn("Срок действия обязателен и не должен превышать 6 месяцев");
      return;
    }
    // Кладём конец дня по UTC, чтобы PAT прожил выбранный день целиком,
    // а не истёк в 00:00 по серверу.
    const expIso = new Date(`${expires}T23:59:59Z`).toISOString();
    if (mockMode) {
      onCreated({
        token_id: "tok_mock",
        token: "dbos_pat_mock_xxxxxxxxxxxx",
        name: name.trim(),
        expires_at: expIso,
      });
      return;
    }
    setPending(true);
    try {
      const resp = await createToken({
        name: name.trim(),
        expires_at: expIso,
        allowed_services: scopes as ServiceName[],
      });
      onCreated(resp);
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось создать токен"));
    } finally {
      setPending(false);
    }
  }

  const submitDisabled =
    pending || noScopes || noServicesAvailable || expiresInvalid;
  const submitTooltip = noServicesAvailable
    ? "У вас нет доступных сервисов для PAT"
    : noScopes
      ? "Минимум 1 сервис"
      : expiresInvalid
        ? "Срок действия обязателен и не должен превышать 6 месяцев"
        : undefined;

  return (
    <div className="card mb-3">
      <div className="font-semibold mb-3">Новый PAT</div>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">name</span>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="cli-token"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            Срок действия (до) — max 6 месяцев
          </span>
          <input
            className="input"
            type="date"
            value={expires}
            min={expBounds.min}
            max={expBounds.max}
            onChange={(e) => setExpires(e.target.value)}
          />
        </label>
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">
            allowed_services — отметьте сервисы, к которым токен будет иметь доступ (минимум 1)
          </span>
          {noServicesAvailable ? (
            <div className="text-xs text-warn italic">
              У вас нет доступных сервисов — PAT создать нельзя.
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {availableServices.map((svc) => (
                <button
                  key={svc}
                  type="button"
                  onClick={() => toggle(svc)}
                  className={`badge ${scopes.includes(svc) ? "active" : ""}`}
                >
                  {svc}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mt-4 flex gap-2 justify-end">
        <button className="btn" onClick={onCancel} disabled={pending}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={submitDisabled}
          title={submitTooltip}
        >
          Создать
        </button>
      </div>
    </div>
  );
}

/**
 * Bounds для UI-поля «Срок действия» PAT/bot-токена.
 *
 * Backend режет `expires_at` < now или > now + 180 дней одним и тем же
 * `INVALID_EXPIRATION` (см. `core/constants.MAX_TOKEN_TTL_DAYS`). Эта
 * функция строит соответствующие `min`/`max` для `<input type="date">`,
 * чтобы юзер физически не мог выставить дату вне диапазона. Default = +90
 * дней — sane середина между «один раз настроил» и «не успел протухнуть».
 *
 * Все три значения — строки `YYYY-MM-DD` (формат для type=date).
 */
function patExpiresBounds(): { min: string; max: string; default: string } {
  return {
    min: mskDateOffset(1),
    max: mskDateOffset(180),
    default: mskDateOffset(90),
  };
}

/**
 * Returns the list of services the user can scope a PAT to. `account_admin`
 * has empty `allowed_services` on the backend (means "all services"); for the
 * UI we expand that into the full `KNOWN_SERVICES` list so chips render.
 */
function resolveAvailableServices(
  user: { allowed_services?: ServiceName[]; platform_role?: string | null } | null,
): string[] {
  if (!user) return [];
  const list = user.allowed_services ?? [];
  if (list.length > 0) return list;
  if (user.platform_role === "account_admin") return [...KNOWN_SERVICES];
  return [];
}

function OneShotPanel({
  resp,
  onClose,
}: {
  resp: PATCreateResponse;
  onClose: () => void;
}) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(resp.token);
      toast.success("Токен скопирован");
    } catch {
      toast.warn("Не удалось скопировать — выделите вручную");
    }
  };
  return (
    <div className="card border-warn mb-3">
      <div className="flex items-center justify-between mb-2">
        <div className="font-semibold text-warn">
          Токен показан один раз — сохраните его сейчас
        </div>
        <button className="btn btn-ghost" onClick={onClose}>
          Скрыть
        </button>
      </div>
      <div className="mono text-xs break-all border border-token p-2 rounded">
        {resp.token}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button className="btn btn-primary flex items-center gap-1" onClick={copy}>
          <Copy className="w-4 h-4" /> Скопировать
        </button>
        <div className="text-[11px] text-dim">
          {resp.name} · {resp.token_id}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

function SessionsCard({ mockMode }: { mockMode: boolean }) {
  const toast = useToast();
  const sessQ = useQuery<SessionListResponse>(
    () => listMySessions(),
    [],
    { enabled: !mockMode },
  );
  const [busy, setBusy] = useState<string | null>(null);

  async function revokeOne(sid: string) {
    if (mockMode) {
      toast.info(`mock: revoke ${sid}`);
      return;
    }
    setBusy(sid);
    try {
      await revokeMySession(sid);
      toast.success("Сессия завершена");
      sessQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось завершить сессию"));
    } finally {
      setBusy(null);
    }
  }

  async function revokeAll() {
    if (mockMode) {
      toast.info("mock: revoke all (except current)");
      return;
    }
    setBusy("all");
    try {
      const r = await revokeAllMySessions(true);
      toast.success(`Отозвано: ${r.revoked_count}`);
      sessQ.refetch();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось отозвать сессии"));
    } finally {
      setBusy(null);
    }
  }

  const sessions = sessQ.data?.items ?? [];

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="font-semibold flex items-center gap-2">
          <Monitor className="w-4 h-4 text-accent" /> Активные сессии
        </div>
        <button
          className="btn btn-danger flex items-center gap-1"
          onClick={revokeAll}
          disabled={busy !== null}
        >
          <ShieldCheck className="w-4 h-4" /> Завершить все, кроме текущей
        </button>
      </div>

      {!mockMode && sessQ.loading && (
        <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
      )}
      {!mockMode && sessQ.error && (
        <div className="alert-danger">{sessQ.error.message}</div>
      )}
      {!mockMode && !sessQ.loading && !sessQ.error && sessions.length === 0 && (
        <div className="empty-card">Сессий нет</div>
      )}
      {mockMode && (
        <div className="text-xs text-dim italic">
          mock-режим: список не подгружается.
        </div>
      )}
      {sessions.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">IP</th>
              <th className="pb-2 pr-3">UA</th>
              <th className="pb-2 pr-3">создана</th>
              <th className="pb-2 pr-3">посл. активность</th>
              <th className="pb-2 pr-3">истекает</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.session_id} className="border-t border-token">
                <td className="py-2 pr-3 mono">{s.ip_address ?? "—"}</td>
                <td className="text-xs truncate max-w-[260px]" title={s.user_agent ?? ""}>
                  {s.user_agent ?? "—"}
                </td>
                <td className="text-xs text-dim mono">{fmtTs(s.created_at)}</td>
                <td className="text-xs text-dim mono">{fmtTs(s.last_used_at)}</td>
                <td className="text-xs text-dim mono">{fmtTs(s.expires_at)}</td>
                <td className="text-right">
                  {s.is_current ? (
                    <span className="badge badge-ok">текущая</span>
                  ) : (
                    <button
                      className="btn btn-danger btn-sm flex items-center gap-1"
                      disabled={busy !== null}
                      onClick={() => revokeOne(s.session_id)}
                    >
                      <LogOut className="w-3 h-3" /> отозвать
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Groups
// ---------------------------------------------------------------------------

function GroupsCard({ mockMode }: { mockMode: boolean }) {
  const { user } = useAuth();
  const uid = user?.user_id ?? "";
  const q = useQuery<Group[]>(
    () => getUserGroups(uid),
    [uid],
    { enabled: !mockMode && Boolean(uid) },
  );

  const items = q.data ?? [];

  return (
    <div className="card">
      <div className="font-semibold flex items-center gap-2 mb-3">
        <UsersRound className="w-4 h-4 text-accent" /> Мои группы
      </div>
      <div className="text-xs text-dim mb-3">
        Членство read-only: добавляет/исключает администратор отдела.
      </div>
      {mockMode && (
        <div className="text-xs text-dim italic">
          mock-режим: список групп не подгружается.
        </div>
      )}
      {!mockMode && !uid && (
        <div className="alert-danger">user_id неизвестен — нет данных для запроса.</div>
      )}
      {!mockMode && uid && q.loading && (
        <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
      )}
      {!mockMode && q.error && (
        <div className="alert-danger">{q.error.message}</div>
      )}
      {!mockMode && !q.loading && !q.error && items.length === 0 && (
        <div className="empty-card">Не состоите ни в одной группе.</div>
      )}
      {items.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-dim text-xs uppercase">
            <tr>
              <th className="pb-2 pr-3">группа</th>
              <th className="pb-2 pr-3">отдел</th>
              <th className="pb-2 pr-3">описание</th>
              <th className="pb-2 pr-3">создана</th>
            </tr>
          </thead>
          <tbody>
            {items.map((g) => (
              <tr key={g.id} className="border-t border-token">
                <td className="py-2 pr-3">
                  <div className="font-medium">{g.name}</div>
                </td>
                <td className="text-xs">
                  <DeptCell deptId={g.department_id} />
                </td>
                <td className="text-xs text-dim">{g.description ?? "—"}</td>
                <td className="text-xs text-dim mono">{fmtTs(g.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Permissions
// ---------------------------------------------------------------------------

function PermissionsCard({ mockMode }: { mockMode: boolean }) {
  const { user } = useAuth();
  const uid = user?.user_id ?? "";
  const q = useQuery<UserPermissionsResponse>(
    () => getUserPermissions(uid),
    [uid],
    { enabled: !mockMode && Boolean(uid) },
  );

  const data = q.data;

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="font-semibold flex items-center gap-2 mb-3">
          <ShieldCheck className="w-4 h-4 text-accent" /> Мои разрешения
        </div>
        <div className="text-xs text-dim mb-3">
          Сводная матрица: какие сервис-роли действуют и откуда они приходят
          (прямое назначение или группа).
        </div>
        {mockMode && (
          <div className="text-xs text-dim italic">
            mock-режим: разрешения не подгружаются.
          </div>
        )}
        {!mockMode && !uid && (
          <div className="alert-danger">user_id неизвестен — нет данных для запроса.</div>
        )}
        {!mockMode && uid && q.loading && (
          <div className="text-xs text-dim py-4 text-center">Загрузка…</div>
        )}
        {!mockMode && q.error && (
          <div className="alert-danger">{q.error.message}</div>
        )}
        {data && <PermissionsView data={data} />}
      </div>
    </div>
  );
}

function PermissionsView({ data }: { data: UserPermissionsResponse }) {
  type Row = {
    service: ServiceName;
    role: string;
    source: string;
    assigned: string | null;
  };
  const rows: Row[] = [];
  for (const r of data.direct_service_roles) {
    rows.push({
      service: r.service_name,
      role: r.role_name,
      source: "прямое назначение",
      assigned: r.assigned_at,
    });
  }
  for (const g of data.groups) {
    for (const r of g.service_roles) {
      rows.push({
        service: r.service_name,
        role: r.role_name,
        source: `группа · ${g.group_name}`,
        assigned: g.joined_at,
      });
    }
  }
  rows.sort((a, b) =>
    a.service === b.service
      ? a.role.localeCompare(b.role)
      : String(a.service).localeCompare(String(b.service)),
  );

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="text-xs text-dim mb-1">allowed_services (effective)</div>
        {data.allowed_services.length === 0 ? (
          <span className="text-xs text-dim italic">— нет доступных сервисов</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {data.allowed_services.map((s) => (
              <span key={s} className="badge">
                {s}
              </span>
            ))}
          </div>
        )}
      </div>

      <div>
        <div className="text-xs text-dim mb-1">service_roles · по источникам</div>
        {rows.length === 0 ? (
          <div className="empty-card">Ролей нет.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">сервис</th>
                <th className="pb-2 pr-3">роль</th>
                <th className="pb-2 pr-3">источник</th>
                <th className="pb-2 pr-3">выдана</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-token">
                  <td className="py-2 pr-3 text-xs">
                    <ServiceInline name={String(r.service)} />
                  </td>
                  <td className="py-2 pr-3">{r.role}</td>
                  <td className="py-2 pr-3 text-xs text-dim">{r.source}</td>
                  <td className="py-2 pr-3 text-xs text-dim mono">
                    {fmtTs(r.assigned)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {data.groups.length > 0 && (
        <div>
          <div className="text-xs text-dim mb-1">access по группам</div>
          <table className="w-full text-sm">
            <thead className="text-left text-dim text-xs uppercase">
              <tr>
                <th className="pb-2 pr-3">группа</th>
                <th className="pb-2 pr-3">отдел</th>
                <th className="pb-2 pr-3">сервисы</th>
                <th className="pb-2 pr-3">вступил</th>
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => (
                <tr key={g.group_id} className="border-t border-token">
                  <td className="py-2 pr-3">
                    <div className="font-medium">{g.group_name}</div>
                  </td>
                  <td className="text-xs">
                    <DeptCell deptId={g.department_id} />
                  </td>
                  <td className="text-xs">
                    {g.service_accesses.length === 0 ? (
                      <span className="text-dim italic">—</span>
                    ) : (
                      <ServiceList names={g.service_accesses.map((a) => a.service_name)} />
                    )}
                  </td>
                  <td className="text-xs text-dim mono">{fmtTs(g.joined_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function HeaderDeptLabel({
  deptName,
  deptId,
}: {
  deptName?: string | null;
  deptId?: string | null;
}) {
  const fromMap = useDeptLabel(deptId);
  if (deptName) return <span>{deptName}</span>;
  if (!deptId) return <span>платформа</span>;
  return <span>{fromMap}</span>;
}

function DeptCell({ deptId }: { deptId: string | null | undefined }) {
  const label = useDeptLabel(deptId);
  return <span>{label}</span>;
}

function ServiceInline({ name }: { name: string }) {
  const label = useServiceLabel(name);
  return <span>{label}</span>;
}

function ServiceList({ names }: { names: string[] }) {
  return (
    <span>
      {names.map((n, i) => (
        <span key={n}>
          {i > 0 && ", "}
          <ServiceInline name={n} />
        </span>
      ))}
    </span>
  );
}

function StatRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 py-1 border-b border-token/40 last:border-0">
      <div className="text-dim text-xs uppercase w-32 shrink-0">{k}</div>
      <div className="text-sm min-w-0 truncate">{v}</div>
    </div>
  );
}

const fmtTs = formatMskShort;
