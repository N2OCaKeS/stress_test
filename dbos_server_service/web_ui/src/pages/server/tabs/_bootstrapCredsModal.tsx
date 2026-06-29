/**
 * Сбор bootstrap-кред для prepare-цикла.
 *
 * Два режима (переключатель):
 *  - «выбрать аккаунт» — список привязанных к серверу и доступных пользователю
 *    server_account'ов (по логину, не id). server_service сам расшифрует пароль
 *    выбранной учётки — UI пароль НЕ шлёт.
 *  - «ввести вручную» — username + пароль (masked) + опциональный приватный
 *    SSH-ключ. Кодирование в base64 централизовано в `bootstrapFormToBody`.
 *
 * `BootstrapCredsFields` — переиспользуемый блок полей (без обёртки-диалога):
 * его встраивают single-prepare (`BootstrapCredsModal`), массовый prepare-batch
 * (`_bulkPrepareModal`) и clean-модалка (`_cleanModal`). `BootstrapCredsModal`
 * остаётся тонкой обёрткой поверх него для одиночного prepare.
 */
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound } from "lucide-react";
import { toBase64 } from "@/lib/base64";
import type { ServerAccount, ServerPrepareRequest } from "@/api/server/types";

/**
 * Редактируемое состояние bootstrap-кред. В отличие от wire-формы держит оба
 * набора (account + manual) одновременно, чтобы переключение режима не теряло
 * введённое. В тело запроса сворачивается через `bootstrapFormToBody`.
 */
export interface BootstrapCredsForm {
  mode: "account" | "manual";
  accountId: string;
  username: string;
  password: string;
  sshPrivateKey: string;
}

/** Стартовое состояние: account-режим, если есть доступные учётки, иначе ручной. */
export function emptyBootstrapForm(accounts: ServerAccount[]): BootstrapCredsForm {
  return {
    mode: accounts.length > 0 ? "account" : "manual",
    accountId: accounts[0]?.id ?? "",
    username: "",
    password: "",
    sshPrivateKey: "",
  };
}

/** Заполнен ли набор кред настолько, чтобы его можно было отправить. */
export function bootstrapFormValid(f: BootstrapCredsForm): boolean {
  return f.mode === "account"
    ? f.accountId.length > 0
    : f.username.trim().length > 0 && f.password.length > 0;
}

/** Свернуть форму в тело запроса (`account_id` либо base64 ручных кред). */
export function bootstrapFormToBody(f: BootstrapCredsForm): ServerPrepareRequest {
  if (f.mode === "account") {
    return { account_id: f.accountId };
  }
  return {
    username_b64: toBase64(f.username.trim()),
    password_b64: toBase64(f.password),
    ...(f.sshPrivateKey.trim()
      ? { ssh_private_key_b64: toBase64(f.sshPrivateKey) }
      : {}),
  };
}

export function BootstrapCredsFields({
  value,
  onChange,
  accounts,
  accountsLoading,
  disabled,
  compact,
}: {
  value: BootstrapCredsForm;
  onChange: (next: BootstrapCredsForm) => void;
  accounts: ServerAccount[];
  accountsLoading?: boolean;
  disabled?: boolean;
  /** Узкий layout для встраивания в per-server строки batch-модалки. */
  compact?: boolean;
}) {
  const patch = (p: Partial<BootstrapCredsForm>) => onChange({ ...value, ...p });
  const noAccounts = accounts.length === 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <button
          type="button"
          className={`btn btn-sm ${value.mode === "account" ? "btn-primary" : ""}`}
          onClick={() => patch({ mode: "account" })}
          disabled={disabled || noAccounts}
          title={
            noAccounts
              ? "У сервера нет привязанных доступных учёток — введите креды вручную"
              : undefined
          }
        >
          Привязанная учётка
        </button>
        <button
          type="button"
          className={`btn btn-sm ${value.mode === "manual" ? "btn-primary" : ""}`}
          onClick={() => patch({ mode: "manual" })}
          disabled={disabled}
        >
          Ручной ввод
        </button>
      </div>

      {value.mode === "account" ? (
        <div>
          <label className="field-label">server_account</label>
          <select
            className="field-input"
            value={value.accountId}
            onChange={(e) => patch({ accountId: e.target.value })}
            disabled={disabled || accountsLoading || noAccounts}
          >
            {noAccounts && <option value="">— нет доступных аккаунтов —</option>}
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
              : noAccounts
                ? "нет привязанных доступных аккаунтов — переключитесь на ручной ввод"
                : "server_service сам расшифрует пароль выбранной учётки (нужно право view_password)"}
          </span>
        </div>
      ) : compact ? (
        <div className="flex flex-col gap-2">
          <div className="grid grid-cols-2 gap-2">
            <input
              className="field-input mono text-xs"
              placeholder="username"
              value={value.username}
              onChange={(e) => patch({ username: e.target.value })}
              disabled={disabled}
              autoComplete="off"
            />
            <input
              className="field-input mono text-xs"
              type="password"
              placeholder="password"
              value={value.password}
              onChange={(e) => patch({ password: e.target.value })}
              disabled={disabled}
              autoComplete="off"
            />
          </div>
          <textarea
            className="field-input mono text-xs"
            rows={2}
            placeholder="SSH private key (опционально)"
            value={value.sshPrivateKey}
            onChange={(e) => patch({ sshPrivateKey: e.target.value })}
            disabled={disabled}
          />
        </div>
      ) : (
        <>
          <div>
            <label className="field-label">username</label>
            <input
              className="field-input mono"
              value={value.username}
              onChange={(e) => patch({ username: e.target.value })}
              disabled={disabled}
              autoComplete="off"
            />
          </div>
          <div>
            <label className="field-label">password</label>
            <input
              className="field-input mono"
              type="password"
              value={value.password}
              onChange={(e) => patch({ password: e.target.value })}
              disabled={disabled}
              autoComplete="off"
            />
          </div>
          <div>
            <label className="field-label">SSH private key (опционально)</label>
            <textarea
              className="field-input mono text-xs"
              rows={3}
              value={value.sshPrivateKey}
              onChange={(e) => patch({ sshPrivateKey: e.target.value })}
              disabled={disabled}
              autoComplete="off"
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;…"
            />
          </div>
        </>
      )}
    </div>
  );
}

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
  onSubmit: (body: ServerPrepareRequest) => Promise<void>;
}) {
  const [form, setForm] = useState<BootstrapCredsForm>(() =>
    emptyBootstrapForm(accounts),
  );
  const [pending, setPending] = useState(false);
  const valid = bootstrapFormValid(form);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pending || !valid) return;
    setPending(true);
    try {
      await onSubmit(bootstrapFormToBody(form));
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

              <BootstrapCredsFields
                value={form}
                onChange={setForm}
                accounts={accounts}
                accountsLoading={accountsLoading}
                disabled={pending}
              />
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
