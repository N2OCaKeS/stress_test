/**
 * Парольная политика логина (`auth_service`, `/admin/password-policy`).
 *
 * Минимальная длина + обязательность буквы/цифры для пользовательских паролей.
 * Хранится в БД, чтобы менять требования без передеплоя. На первом запуске
 * сервиса сидируется из env (`AUTH_PASSWORD_POLICY_*`), дальше — из БД.
 * INITIAL_ADMIN_PASSWORD этой политике НЕ подчиняется (жёсткий guard = 12).
 *
 * Источник истины — `GET/PUT /api/auth/v1/admin/password-policy`. Гейтится
 * account_admin; остальным backend отвечает 403, поэтому пункт каталога
 * показывается только account_admin (см. adminCatalog).
 */

import { useEffect, useMemo, useState } from "react";
import { KeyRound, AlertCircle } from "lucide-react";

import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getPasswordPolicy,
  putPasswordPolicy,
  MIN_CONFIGURABLE_LENGTH,
  MAX_CONFIGURABLE_LENGTH,
  type PasswordPolicy,
} from "@/api/auth/passwordPolicy";

interface FormState {
  minLength: string;
  requireLetter: boolean;
  requireDigit: boolean;
}

function toForm(p: PasswordPolicy): FormState {
  return {
    minLength: String(p.min_length),
    requireLetter: p.require_letter,
    requireDigit: p.require_digit,
  };
}

function validate(form: FormState): string | null {
  const n = Number(form.minLength);
  if (!Number.isInteger(n)) {
    return "Минимальная длина — целое число.";
  }
  if (n < MIN_CONFIGURABLE_LENGTH || n > MAX_CONFIGURABLE_LENGTH) {
    return `Длина в диапазоне ${MIN_CONFIGURABLE_LENGTH}..${MAX_CONFIGURABLE_LENGTH}.`;
  }
  return null;
}

function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.status === 422)
      return `Backend отклонил значение (длина ${MIN_CONFIGURABLE_LENGTH}..${MAX_CONFIGURABLE_LENGTH}).`;
  }
  return apiErrMsg(e, "Не удалось сохранить политику");
}

export function ServicesPasswordPolicy() {
  const toast = useToast();
  const cfgQ = useQuery<PasswordPolicy>(() => getPasswordPolicy(), []);
  const loaded = cfgQ.data;

  const [form, setForm] = useState<FormState | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (loaded) setForm(toForm(loaded));
  }, [loaded]);

  const error = useMemo(() => (form ? validate(form) : null), [form]);

  const dirty = useMemo(() => {
    if (!loaded || !form) return false;
    return JSON.stringify(toForm(loaded)) !== JSON.stringify(form);
  }, [loaded, form]);

  function patch(next: Partial<FormState>) {
    setForm((prev) => (prev ? { ...prev, ...next } : prev));
  }

  async function handleSave() {
    if (pending || !form || error) return;
    setPending(true);
    try {
      await putPasswordPolicy({
        min_length: Number(form.minLength),
        require_letter: form.requireLetter,
        require_digit: form.requireDigit,
      });
      toast.success("Парольная политика сохранена");
      cfgQ.refetch();
    } catch (err) {
      toast.error(saveError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <KeyRound className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Парольная политика
          </h1>
          <div className="text-xs text-dim">
            требования к паролям логина (создание и смена)
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-5">
        {cfgQ.loading && cfgQ.data == null && (
          <div className="text-xs text-dim text-center py-4">Загрузка…</div>
        )}

        {!cfgQ.loading && cfgQ.error != null && (
          <div className="alert-danger text-sm flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div className="flex-1">
              <div>{apiErrMsg(cfgQ.error, "Политика не загрузилась")}</div>
              <button
                className="btn btn-ghost mt-2"
                onClick={() => cfgQ.refetch()}
                type="button"
              >
                Повторить
              </button>
            </div>
          </div>
        )}

        {form != null && (
          <>
            <div className="flex flex-col gap-4 max-w-md">
              <label className="flex flex-col gap-1">
                <span className="text-sm">Минимальная длина</span>
                <span className="text-xs text-dim">
                  диапазон {MIN_CONFIGURABLE_LENGTH}..{MAX_CONFIGURABLE_LENGTH}
                </span>
                <input
                  type="number"
                  className="field-input w-32"
                  min={MIN_CONFIGURABLE_LENGTH}
                  max={MAX_CONFIGURABLE_LENGTH}
                  value={form.minLength}
                  onChange={(e) => patch({ minLength: e.target.value })}
                  disabled={pending}
                />
              </label>

              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.requireLetter}
                  onChange={(e) => patch({ requireLetter: e.target.checked })}
                  disabled={pending}
                />
                Требовать хотя бы одну букву
              </label>

              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.requireDigit}
                  onChange={(e) => patch({ requireDigit: e.target.checked })}
                  disabled={pending}
                />
                Требовать хотя бы одну цифру
              </label>
            </div>

            <div className="text-[11px] text-dim max-w-md">
              Пароль первого администратора (`INITIAL_ADMIN_PASSWORD`) этой
              политике не подчиняется — для него всегда действует жёсткое
              требование в 12 символов.
            </div>

            {error && (
              <div className="alert-warn text-xs flex items-start gap-2 max-w-md">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                <div>{error}</div>
              </div>
            )}

            <div className="flex items-center gap-3">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSave}
                disabled={pending || !dirty || error != null}
              >
                {pending ? "Сохраняем…" : "Сохранить"}
              </button>
              {dirty && !pending && (
                <span className="text-xs text-dim">
                  есть несохранённые изменения
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
