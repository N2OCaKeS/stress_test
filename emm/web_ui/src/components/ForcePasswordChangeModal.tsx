import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, Check, ShieldAlert } from "lucide-react";
import { ApiError } from "@/api/client";
import { changeMyPassword } from "@/api/auth/users";
import { useAuth } from "@/contexts/AuthContext";
import {
  passwordPolicyMessage,
  getActivePasswordPolicy,
} from "@/lib/passwordPolicy";

/**
 * Blocking modal shown when `IdentityContext.must_change_password === true`.
 *
 * Unclosable by design: no close button, Esc and overlay clicks suppressed.
 * After a successful `POST /users/me/password` the backend revokes the
 * current session (along with all others). The modal then immediately
 * re-authenticates with the new password to obtain a fresh token pair so
 * the user stays in the app without bouncing through /login. The fresh
 * /me payload has `must_change_password=false`, so the modal unmounts
 * itself via `AuthProvider`'s conditional render.
 */
export function ForcePasswordChangeModal() {
  const auth = useAuth();
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pol = getActivePasswordPolicy();
  const minLen = pol.minLength;
  const hasLetter = /[A-Za-zА-Яа-яЁё]/.test(newPwd);
  const hasDigit = /\d/.test(newPwd);
  const mismatch = confirm.length > 0 && newPwd !== confirm;
  const tooShort = newPwd.length > 0 && newPwd.length < minLen;
  const noLetter = newPwd.length > 0 && pol.requireLetter && !hasLetter;
  const noDigit = newPwd.length > 0 && pol.requireDigit && !hasDigit;
  const policyOk =
    newPwd.length >= minLen &&
    (!pol.requireLetter || hasLetter) &&
    (!pol.requireDigit || hasDigit);
  const canSubmit =
    !busy && oldPwd.length > 0 && policyOk && newPwd === confirm;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!canSubmit) return;
    setBusy(true);
    try {
      await changeMyPassword({ old_password: oldPwd, new_password: newPwd });
      // POST /users/me/password revokes the current session — without an
      // immediate re-login the next request would 401 and bounce us to the
      // login screen. Re-authenticate inline with the new password.
      const username = auth.user?.username;
      if (!username) {
        throw new Error("Не удалось определить username для re-login");
      }
      await auth.login({ username, password: newPwd });
      // Successful login replaces tokens and re-fetches /me; the new
      // identity has must_change_password=false, so this modal unmounts.
    } catch (e) {
      setErr(
        e instanceof ApiError ? `${e.errorCode}: ${e.message}` : String(e),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog.Root open modal>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => e.preventDefault()}
        >
          <div className="modal-header">
            <ShieldAlert className="w-5 h-5 text-warn" />
            <Dialog.Title className="text-base font-semibold">
              Требуется смена пароля
            </Dialog.Title>
          </div>
          <form onSubmit={submit}>
            <div className="modal-body">
              <Dialog.Description className="text-sm text-dim mb-3">
                Администратор сбросил ваш пароль или это ваш первый вход. Перед
                продолжением работы задайте новый пароль.
              </Dialog.Description>
              <div className="grid gap-2">
                <div>
                  <label className="field-label">Текущий пароль</label>
                  <input
                    type="password"
                    className="field-input"
                    autoFocus
                    value={oldPwd}
                    onChange={(e) => setOldPwd(e.target.value)}
                    autoComplete="current-password"
                  />
                </div>
                <div>
                  <label className="field-label">
                    Новый пароль ({passwordPolicyMessage()})
                  </label>
                  <input
                    type="password"
                    className="field-input"
                    value={newPwd}
                    onChange={(e) => setNewPwd(e.target.value)}
                    autoComplete="new-password"
                  />
                  {tooShort && (
                    <div className="text-[11px] text-danger mt-1">
                      минимум {minLen} символов
                    </div>
                  )}
                  {!tooShort && noLetter && (
                    <div className="text-[11px] text-danger mt-1">
                      нужна минимум одна буква
                    </div>
                  )}
                  {!tooShort && !noLetter && noDigit && (
                    <div className="text-[11px] text-danger mt-1">
                      нужна минимум одна цифра
                    </div>
                  )}
                </div>
                <div>
                  <label className="field-label">Повторите новый пароль</label>
                  <input
                    type="password"
                    className="field-input"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    autoComplete="new-password"
                  />
                  {mismatch && (
                    <div className="text-[11px] text-danger mt-1">
                      пароли не совпадают
                    </div>
                  )}
                </div>
              </div>
              {err && <div className="alert-danger mt-3">{err}</div>}
              <div className="text-[11px] text-dim mt-3 flex items-start gap-1">
                <KeyRound className="w-3 h-3 mt-0.5 shrink-0" />
                <span>
                  После смены все активные сессии будут отозваны, включая
                  текущую. Модалка сразу переподнимет вход с новым паролем.
                </span>
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="submit"
                className="btn btn-primary flex items-center gap-1"
                disabled={!canSubmit}
              >
                <Check className="w-4 h-4" />
                {busy ? "..." : "Сменить пароль"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
