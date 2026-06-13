/**
 * Модалка ввода bootstrap-кред для prepare-цикла.
 *
 * Раньше username/password собирались двумя `window.prompt`, из-за чего пароль
 * висел на экране открытым текстом (prompt не умеет маскировать). Здесь пароль
 * идёт через `<input type="password">`, username — обычным полем. Кодирование в
 * base64 и сам вызов prepare остаются на стороне `manage.tsx` — модалка только
 * собирает и валидирует ввод, отдавая его наверх через `onSubmit`.
 */
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound } from "lucide-react";

export function BootstrapCredsModal({
  hostname,
  onClose,
  onSubmit,
}: {
  hostname: string;
  onClose: () => void;
  onSubmit: (creds: { username: string; password: string }) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);

  const valid = username.trim().length > 0 && password.length > 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending || !valid) return;
    setPending(true);
    try {
      await onSubmit({ username: username.trim(), password });
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
                Логин и пароль учётки, под которой worker зайдёт на{" "}
                <span className="mono">{hostname}</span> для запуска
                management-цикла. Передаются один раз, на сервере не хранятся.
              </Dialog.Description>

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

              <div>
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
