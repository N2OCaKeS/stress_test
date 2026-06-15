/**
 * Модалка выбора bootstrap-кред для prepare-цикла.
 *
 * Два режима (переключатель):
 *  - «выбрать аккаунт» — список привязанных к серверу и доступных пользователю
 *    server_account'ов (по логину, не id). server_service сам расшифрует пароль
 *    выбранной учётки — UI пароль НЕ шлёт.
 *  - «ввести вручную» — username + пароль (masked) + опциональный приватный
 *    SSH-ключ. Кодирование в base64 и сам вызов prepare — на стороне `manage.tsx`;
 *    модалка только собирает выбор/ввод и отдаёт его наверх через `onSubmit`.
 */
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound } from "lucide-react";
import type { ServerAccount } from "@/api/server/types";

export type BootstrapCreds =
  | { mode: "account"; accountId: string }
  | {
      mode: "manual";
      username: string;
      password: string;
      sshPrivateKey: string;
    };

export function BootstrapCredsModal({
  hostname,
  accounts,
  accountsLoading,
  onClose,
  onSubmit,
}: {
  hostname: string;
  accounts: ServerAccount[];
  accountsLoading: boolean;
  onClose: () => void;
  onSubmit: (creds: BootstrapCreds) => Promise<void>;
}) {
  const [mode, setMode] = useState<"account" | "manual">(
    accounts.length > 0 ? "account" : "manual",
  );
  const [accountId, setAccountId] = useState<string>(accounts[0]?.id ?? "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sshPrivateKey, setSshPrivateKey] = useState("");
  const [pending, setPending] = useState(false);

  const validAccount = mode === "account" && accountId.length > 0;
  const validManual =
    mode === "manual" && username.trim().length > 0 && password.length > 0;
  const valid = validAccount || validManual;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending || !valid) return;
    setPending(true);
    try {
      if (mode === "account") {
        await onSubmit({ mode: "account", accountId });
      } else {
        await onSubmit({
          mode: "manual",
          username: username.trim(),
          password,
          sshPrivateKey,
        });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog.Root open modal onOpenChange={(o) => !o && !pending && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          onInteractOutside={(e) => pending && e.preventDefault()}
          onEscapeKeyDown={(e) => pending && e.preventDefault()}
        >
          <div className="modal-header">
            <KeyRound className="w-5 h-5 text-accent" />
            <Dialog.Title className="text-base font-semibold">
              Bootstrap-креды для prepare
            </Dialog.Title>
          </div>

          <form onSubmit={submit}>
            <div className="modal-body">
              <Dialog.Description className="text-sm text-dim mb-3">
                Креды, под которыми worker зайдёт на{" "}
                <span className="mono">{hostname}</span> для запуска
                management-цикла. Передаются один раз, на сервере не хранятся.
              </Dialog.Description>

              <div className="flex gap-2 mb-3">
                <button
                  type="button"
                  className={`btn ${mode === "account" ? "btn-primary" : ""}`}
                  onClick={() => setMode("account")}
                  disabled={pending}
                >
                  Выбрать аккаунт
                </button>
                <button
                  type="button"
                  className={`btn ${mode === "manual" ? "btn-primary" : ""}`}
                  onClick={() => setMode("manual")}
                  disabled={pending}
                >
                  Ввести вручную
                </button>
              </div>

              {mode === "account" ? (
                <div>
                  <label className="field-label">server_account</label>
                  <select
                    className="field-input"
                    value={accountId}
                    onChange={(e) => setAccountId(e.target.value)}
                    disabled={pending || accountsLoading}
                  >
                    {accounts.length === 0 && (
                      <option value="">— нет доступных аккаунтов —</option>
                    )}
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.login}
                        {a.has_sudo ? " (sudo)" : ""}
                        {a.source === "discovered" ? " · discovered" : ""}
                      </option>
                    ))}
                  </select>
                  <span className="text-[11px] text-dim block mt-1">
                    {accountsLoading
                      ? "загружаем привязанные аккаунты…"
                      : accounts.length === 0
                        ? "нет привязанных доступных аккаунтов — введите креды вручную"
                        : "server_service сам расшифрует пароль выбранной учётки (нужно право view_password)"}
                  </span>
                </div>
              ) : (
                <>
                  <div className="mb-3">
                    <label className="field-label">username</label>
                    <input
                      className="field-input mono"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      disabled={pending}
                      autoComplete="off"
                      autoFocus
                    />
                  </div>

                  <div className="mb-3">
                    <label className="field-label">password</label>
                    <input
                      className="field-input mono"
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      disabled={pending}
                      autoComplete="off"
                    />
                  </div>

                  <div>
                    <label className="field-label">
                      SSH private key (опционально)
                    </label>
                    <textarea
                      className="field-input mono text-xs"
                      rows={3}
                      value={sshPrivateKey}
                      onChange={(e) => setSshPrivateKey(e.target.value)}
                      disabled={pending}
                      autoComplete="off"
                      placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;…"
                    />
                  </div>
                </>
              )}
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn"
                onClick={onClose}
                disabled={pending}
              >
                Отмена
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={pending || !valid}
              >
                {pending ? "Запускаем…" : "Запустить prepare"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
