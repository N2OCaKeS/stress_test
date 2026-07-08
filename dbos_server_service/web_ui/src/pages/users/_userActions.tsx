import { useMemo, useState, type ReactNode } from "react";
import { X, UserCog, ShieldCheck, Copy, AlertTriangle, RefreshCw } from "lucide-react";
import { ApiError, apiErrMsg } from "@/api/client";
import { formatMskDate } from "@/lib/datetime";
import { useTimeoutRef } from "@/lib/useTimeoutRef";
import { createUser, updateUser } from "@/api/auth/users";
import { createGroup } from "@/api/auth/groups";
import { createBot, issueBotToken } from "@/api/auth/bots";
import {
  isValidEmail,
  PASSWORD_POLICY_MESSAGE,
  validatePassword,
  validateUsername,
} from "@/lib/passwordPolicy";
import type {
  Department,
  PlatformRole,
  BotTokenCreateResponse,
} from "@/api/auth/types";

/**
 * Lightweight modal + create/edit forms for users-account_admin workzone.
 * Intentionally self-contained (no dependence on InlineEditor scaffolding
 * from admin/services/ServicesUsers): this page renders a free-form workzone,
 * not the inline-editor split layout.
 */

// Password generator — produces a 16-char string that satisfies the
// auth_service policy (>=12 chars, at least one letter and one digit). The
// admin doesn't pick the initial password — must_change_password=True on
// create forces the user to set their own at first login.
function generateInitialPassword(): string {
  const lower = "abcdefghijkmnpqrstuvwxyz";
  const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const digits = "23456789";
  const symbols = "!@#$%&*-_+=";
  const all = lower + upper + digits + symbols;
  const pick = (alphabet: string) =>
    alphabet[Math.floor(Math.random() * alphabet.length)];
  // Guarantee at least one of each policy-relevant class, fill to 16 chars.
  const chars = [pick(lower), pick(upper), pick(digits), pick(symbols)];
  while (chars.length < 16) chars.push(pick(all));
  // Shuffle so the first 4 characters are not always in class order.
  for (let i = chars.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div
        className="surface border border-token rounded-lg shadow-xl w-full max-w-xl mx-4 max-h-[90vh] overflow-y-auto"
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between border-b border-token px-4 py-3">
          <h3 className="font-semibold text-sm flex items-center gap-2">
            {title}
          </h3>
          <button className="btn btn-ghost p-1" onClick={onClose} aria-label="Закрыть">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

type DeptLite = Pick<Department, "id" | "name">;

export function CreateUserForm({
  depts,
  mockMode,
  onSuccess,
  onCancel,
}: {
  depts: DeptLite[];
  mockMode: boolean;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [username, setUsername] = useState("");
  const [lastName, setLastName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [password, setPassword] = useState(() => generateInitialPassword());
  const [email, setEmail] = useState("");
  const [dept, setDept] = useState("");
  const [platformRole, setPlatformRole] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copyHint, setCopyHint] = useState(false);
  const setCopyHintTimeout = useTimeoutRef();

  // Platform roles available depend on whether a department is picked:
  // account_admin and loging_admin/loging_reader are platform-wide and do not
  // accept department_id; department_admin is per-dept and requires it.
  const availableRoles = useMemo(() => {
    if (dept) {
      return [{ value: "department_admin", label: "department_admin" }];
    }
    return [
      { value: "account_admin", label: "account_admin" },
      { value: "loging_admin", label: "loging_admin" },
      { value: "loging_reader", label: "loging_reader" },
    ];
  }, [dept]);

  // If the user picks a dept that invalidates the current platform_role
  // selection, reset it.
  if (platformRole && !availableRoles.some((r) => r.value === platformRole)) {
    setPlatformRole("");
  }

  // Клиентская валидация — зеркало UserCreate-схемы auth_service. Не пускаем
  // заведомо-422 запросы (username pattern/длина, password-policy, email).
  const usernameError = username ? validateUsername(username) : null;
  const passwordError = validatePassword(password);
  const emailError = email && !isValidEmail(email) ? "Неверный формат email" : null;
  // department_id обязателен для всех, кроме платформенных admin-ролей
  // (account_admin / loging_admin / loging_reader). Обычный user без отдела
  // и department_admin без отдела backend отбивает MISSING_REQUIRED_FIELD.
  const PLATFORM_ADMIN_ROLES = ["account_admin", "loging_admin", "loging_reader"];
  const needsDept = !PLATFORM_ADMIN_ROLES.includes(platformRole);
  const deptError = needsDept && !dept ? "Выберите отдел" : null;
  const formInvalid =
    !username ||
    !password ||
    !!usernameError ||
    !!passwordError ||
    !!emailError ||
    !!deptError;

  function copyPassword() {
    navigator.clipboard?.writeText(password).catch(() => {});
    setCopyHint(true);
    setCopyHintTimeout(() => setCopyHint(false), 1500);
  }

  async function submit() {
    if (mockMode) {
      onSuccess();
      return;
    }
    if (formInvalid) return;
    setBusy(true);
    setErr(null);
    try {
      // must_change_password=true прямо в create-body — выданный admin'ом
      // временный пароль не должен остаться постоянным (форма обещает «сменит
      // при первом входе»). Передаём атомарно, без отдельного вызова.
      await createUser({
        username,
        password,
        last_name: lastName.trim() || null,
        first_name: firstName.trim() || null,
        middle_name: middleName.trim() || null,
        email: email || undefined,
        department_id: dept || null,
        platform_role: (platformRole || null) as PlatformRole,
        must_change_password: true,
      });
      onSuccess();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="username">
        <div>
          <input
            className="input w-full"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            maxLength={128}
          />
          {usernameError && (
            <div className="text-[11px] text-danger mt-1">{usernameError}</div>
          )}
        </div>
      </Field>
      <Field label="Фамилия">
        <input
          className="input w-full"
          value={lastName}
          onChange={(e) => setLastName(e.target.value)}
          maxLength={128}
          placeholder="Иванов"
        />
      </Field>
      <Field label="Имя">
        <input
          className="input w-full"
          value={firstName}
          onChange={(e) => setFirstName(e.target.value)}
          maxLength={128}
          placeholder="Иван"
        />
      </Field>
      <Field label="Отчество">
        <input
          className="input w-full"
          value={middleName}
          onChange={(e) => setMiddleName(e.target.value)}
          maxLength={128}
          placeholder="Иванович"
        />
      </Field>
      <Field label={`начальный пароль (сгенерирован) · ${PASSWORD_POLICY_MESSAGE}`}>
        <div className="flex gap-2 items-center">
          <input
            className="input flex-1 mono"
            type="text"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button
            type="button"
            className="btn"
            onClick={() => setPassword(generateInitialPassword())}
            title="Сгенерировать новый"
            disabled={busy}
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            type="button"
            className="btn"
            onClick={copyPassword}
            title="Скопировать"
            disabled={busy}
          >
            <Copy className="w-4 h-4" />
          </button>
        </div>
        {passwordError ? (
          <div className="text-[11px] text-danger mt-1">{passwordError}</div>
        ) : (
          <div className="text-[11px] text-dim mt-1">
            {copyHint
              ? "Скопировано"
              : "Пользователь обязан сменить пароль при первом входе"}
          </div>
        )}
      </Field>
      <Field label="email">
        <div>
          <input
            className="input w-full"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {emailError && (
            <div className="text-[11px] text-danger mt-1">{emailError}</div>
          )}
        </div>
      </Field>
      <Field label="dept">
        <div>
          <select
            className="input"
            value={dept}
            onChange={(e) => setDept(e.target.value)}
          >
            <option value="">— (платформенный)</option>
            {depts.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          {deptError && (
            <div className="text-[11px] text-danger mt-1">
              {deptError} — без отдела можно создать только платформенную роль
              (account_admin / loging_admin / loging_reader).
            </div>
          )}
        </div>
      </Field>
      <Field label="platform_role">
        <select
          className="input"
          value={platformRole}
          onChange={(e) => setPlatformRole(e.target.value)}
        >
          <option value="">— (обычный пользователь)</option>
          {availableRoles.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
        <div className="text-[11px] text-dim mt-1">
          {dept
            ? "Когда выбран отдел — доступна только dep-роль."
            : "Платформенные роли — без отдела."}
        </div>
        <div className="text-[11px] text-dim mt-1">
          Платформенная роль определяет доступ к{" "}
          <span className="mono">loging_service</span>. Для чтения аудита
          назначь <span className="mono">loging_reader</span>, для управления
          rules/retention — <span className="mono">loging_admin</span>. Эти
          роли работают cross-dept и не требуют отдела.
        </div>
      </Field>
      {err && <div className="alert-danger text-xs">{err}</div>}
      {mockMode && (
        <div className="text-[11px] text-dim">
          mock-режим: POST в backend не выполняется.
        </div>
      )}
      <div className="flex gap-2 justify-end mt-2">
        <button className="btn" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={busy || (!mockMode && formInvalid)}
        >
          {busy ? "..." : "Создать"}
        </button>
      </div>
    </div>
  );
}

export function EditRolesForm({
  user,
  depts,
  mockMode,
  onSuccess,
  onCancel,
}: {
  user: {
    id: string;
    username: string;
    platform_role: PlatformRole;
    dept_id: string | null;
    last_name?: string | null;
    first_name?: string | null;
    middle_name?: string | null;
  };
  depts: DeptLite[];
  mockMode: boolean;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [platformRole, setPlatformRole] = useState<string>(user.platform_role ?? "");
  const [dept, setDept] = useState<string>(user.dept_id ?? "");
  const [lastName, setLastName] = useState<string>(user.last_name ?? "");
  const [firstName, setFirstName] = useState<string>(user.first_name ?? "");
  const [middleName, setMiddleName] = useState<string>(user.middle_name ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (mockMode) {
      onSuccess();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const body: {
        platform_role?: PlatformRole;
        department_id?: string | null;
        last_name?: string | null;
        first_name?: string | null;
        middle_name?: string | null;
      } = {};
      const nextRole = (platformRole || null) as PlatformRole;
      if (nextRole !== (user.platform_role ?? null)) body.platform_role = nextRole;
      const nextDept = dept || null;
      if (nextDept !== (user.dept_id ?? null)) body.department_id = nextDept;
      // ФИО: пустое поле шлём как null (явная очистка), непустое — trim.
      const nextLast = lastName.trim() || null;
      if (nextLast !== (user.last_name ?? null)) body.last_name = nextLast;
      const nextFirst = firstName.trim() || null;
      if (nextFirst !== (user.first_name ?? null)) body.first_name = nextFirst;
      const nextMiddle = middleName.trim() || null;
      if (nextMiddle !== (user.middle_name ?? null)) body.middle_name = nextMiddle;
      if (Object.keys(body).length > 0) {
        await updateUser(user.id, body);
      }
      onSuccess();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="text-xs text-dim flex items-center gap-2">
        <UserCog className="w-3 h-3" /> {user.username}
        <span className="mono">{user.id}</span>
      </div>
      <Field label="Фамилия">
        <input
          className="input"
          value={lastName}
          onChange={(e) => setLastName(e.target.value)}
          maxLength={128}
          placeholder="Иванов"
        />
      </Field>
      <Field label="Имя">
        <input
          className="input"
          value={firstName}
          onChange={(e) => setFirstName(e.target.value)}
          maxLength={128}
          placeholder="Иван"
        />
      </Field>
      <Field label="Отчество">
        <input
          className="input"
          value={middleName}
          onChange={(e) => setMiddleName(e.target.value)}
          maxLength={128}
          placeholder="Иванович"
        />
      </Field>
      <Field label="platform_role">
        <select
          className="input"
          value={platformRole}
          onChange={(e) => setPlatformRole(e.target.value)}
        >
          <option value="">— (обычный пользователь)</option>
          <option value="account_admin">account_admin</option>
          <option value="department_admin">department_admin</option>
          <option value="loging_admin">loging_admin</option>
          <option value="loging_reader">loging_reader</option>
        </select>
        <div className="text-[11px] text-dim mt-1">
          Платформенная роль определяет доступ к{" "}
          <span className="mono">loging_service</span>. Для чтения аудита —{" "}
          <span className="mono">loging_reader</span>, для управления
          rules/retention — <span className="mono">loging_admin</span>. Эти
          роли работают cross-dept и не требуют отдела.
        </div>
      </Field>
      <Field label="dept">
        <select
          className="input"
          value={dept}
          onChange={(e) => setDept(e.target.value)}
        >
          <option value="">— (платформенный)</option>
          {depts.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </Field>
      <div className="text-[11px] text-dim flex items-center gap-1">
        <ShieldCheck className="w-3 h-3" />
        Service-роли управляются в admin/services - secret/server/worker/loging.
      </div>
      {err && <div className="alert-danger text-xs">{err}</div>}
      {mockMode && (
        <div className="text-[11px] text-dim">
          mock-режим: PATCH в backend не выполняется.
        </div>
      )}
      <div className="flex gap-2 justify-end mt-2">
        <button className="btn" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? "..." : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid grid-cols-[140px_1fr] items-center gap-3 text-sm">
      <span className="text-dim">{label}</span>
      {children}
    </label>
  );
}

export function CreateGroupForm({
  depts,
  defaultDeptId,
  mockMode,
  onSuccess,
  onCancel,
}: {
  depts: DeptLite[];
  defaultDeptId?: string | null;
  mockMode: boolean;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [deptId, setDeptId] = useState<string>(defaultDeptId ?? "");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (mockMode) {
      onSuccess();
      return;
    }
    if (!name || !deptId) return;
    setBusy(true);
    setErr(null);
    try {
      await createGroup({
        department_id: deptId,
        name,
        description: description || undefined,
      });
      onSuccess();
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="name">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          placeholder="dba-engineers"
        />
      </Field>
      <Field label="dept">
        <select
          className="input"
          value={deptId}
          onChange={(e) => setDeptId(e.target.value)}
        >
          <option value="">— выберите dept —</option>
          {depts.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="description">
        <input
          className="input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      {err && <div className="alert-danger text-xs">{err}</div>}
      {mockMode && (
        <div className="text-[11px] text-dim">
          mock-режим: POST в backend не выполняется.
        </div>
      )}
      <div className="flex gap-2 justify-end mt-2">
        <button className="btn" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={busy || (!mockMode && (!name || !deptId))}
        >
          {busy ? "..." : "Создать"}
        </button>
      </div>
    </div>
  );
}

export function CreateBotForm({
  depts,
  defaultDeptId,
  mockMode,
  onSuccess,
  onCancel,
}: {
  depts: DeptLite[];
  defaultDeptId?: string | null;
  mockMode: boolean;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [deptId, setDeptId] = useState<string>(defaultDeptId ?? "");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [token, setToken] = useState<BotTokenCreateResponse | null>(null);
  const [copied, setCopied] = useState(false);
  const setCopiedTimeout = useTimeoutRef();

  async function submit() {
    if (mockMode) {
      // mock: do not contact backend, just bubble up success
      onSuccess();
      return;
    }
    if (!name || !deptId) return;
    setBusy(true);
    setErr(null);
    try {
      const bot = await createBot({
        name,
        department_id: deptId,
        allowed_services: [],
        description: description || undefined,
      });
      // Try issuing the initial one-time token. If it fails the bot is still
      // created — surface the error but keep the modal open for the user to
      // retry via the rotate flow later. expires_at обязателен по политике
      // (бессрочные bot-токены запрещены, max 6 мес) — берём +90 дней.
      try {
        const exp = new Date();
        exp.setDate(exp.getDate() + 90);
        const issued = await issueBotToken(bot.id, {
          name: "initial",
          expires_at: exp.toISOString(),
        });
        setToken(issued);
      } catch (te) {
        setErr(
          te instanceof ApiError
            ? `bot создан, но токен не выдан: ${te.errorCode}: ${te.message}`
            : `bot создан, но токен не выдан: ${String(te)}`,
        );
      }
    } catch (e) {
      setErr(apiErrMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function copyToken() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied(true);
      setCopiedTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard API can be unavailable in some browsers/contexts
    }
  }

  if (token) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-2 border border-token rounded p-2 text-xs text-warn">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            Токен показывается <b>один раз</b>. Скопируйте сейчас — после
            закрытия модалки восстановить его будет невозможно.
          </div>
        </div>
        <Field label="token_id">
          <span className="mono text-xs">{token.token_id}</span>
        </Field>
        <Field label="token">
          <div className="flex items-center gap-2 min-w-0">
            <code className="mono text-xs break-all flex-1 px-2 py-1 surface-2 rounded border border-token">
              {token.token}
            </code>
            <button
              className="btn flex items-center gap-1 shrink-0"
              onClick={copyToken}
            >
              <Copy className="w-3 h-3" />
              {copied ? "скопировано" : "копировать"}
            </button>
          </div>
        </Field>
        {token.expires_at && (
          <Field label="expires_at">
            <span className="mono text-xs">{formatMskDate(token.expires_at)}</span>
          </Field>
        )}
        <div className="flex justify-end mt-2">
          <button className="btn btn-primary" onClick={onSuccess}>
            Готово
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="name">
        <input
          className="input mono"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          maxLength={256}
          placeholder="bot-ci-core"
        />
      </Field>
      <Field label="owner_dept">
        <select
          className="input"
          value={deptId}
          onChange={(e) => setDeptId(e.target.value)}
        >
          <option value="">— выберите dept —</option>
          {depts.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="description">
        <input
          className="input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      <div className="text-[11px] text-dim">
        Allowed services и роли назначаются после создания — в карточке бота.
      </div>
      {err && <div className="alert-danger text-xs">{err}</div>}
      {mockMode && (
        <div className="text-[11px] text-dim">
          mock-режим: POST в backend не выполняется.
        </div>
      )}
      <div className="flex gap-2 justify-end mt-2">
        <button className="btn" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        <button
          className="btn btn-primary"
          onClick={submit}
          disabled={busy || (!mockMode && (!name || !deptId))}
        >
          {busy ? "..." : "Создать"}
        </button>
      </div>
    </div>
  );
}
