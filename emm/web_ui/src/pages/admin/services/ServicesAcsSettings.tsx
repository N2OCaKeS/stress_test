/**
 * Настройки доступа к ACS (`/admin/services.server.acs_settings`).
 *
 * ACS — внешний сервис снимков дисков физических серверов (обёртка над
 * Clonezilla). Платформенный singleton: кил-свитч `enabled`, `acs_url` и
 * пароль clonezilla-сервера (write-only, GET отдаёт только факт
 * `password_is_set`). Ниже — таблица отделов с флагом `is_enabled`: per-
 * department opt-in поверх обычной action-матрицы server_service (сам
 * dispatch создания/восстановления снимков проверяет этот флаг отдельно).
 *
 * Источник истины — `server_service`:
 *   GET/PUT /api/server/v1/settings/acs
 *   GET/PUT /api/server/v1/settings/acs/departments
 * Гейтится account_admin; остальным backend режет на 403, поэтому пункт
 * каталога показывается только account_admin (см. adminCatalog).
 *
 * `/settings/acs/departments` не несёт имён отделов — они подтягиваются
 * отдельно через `listDepartments()` (auth_service) и сшиваются по
 * `department_id`, как и в `ServicesNavLink`.
 */

import { useEffect, useMemo, useState } from "react";
import { Camera, AlertCircle, AlertTriangle } from "lucide-react";

import { Checkbox } from "@/components/ui/Checkbox";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getAcsSettings,
  updateAcsSettings,
  getAcsDepartmentAccess,
  updateAcsDepartmentAccess,
  type AcsSettings,
  type AcsDepartmentAccessItem,
} from "@/api/server/acsSettings";
import { listDepartments } from "@/api/auth/departments";
import type { Department } from "@/api/auth/types";
import { Button } from "@/components/ui/Button";

function settingsSaveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.errorCode === "ACS_ENABLE_REQUIRES_CONFIG")
      return "Нельзя включить ACS без сохранённых URL и пароля.";
    if (e.status === 422) return "Backend отклонил значения (проверьте URL).";
  }
  return apiErrMsg(e, "Не удалось сохранить настройки ACS");
}

function departmentsSaveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.status === 422) return "Backend отклонил список отделов.";
  }
  return apiErrMsg(e, "Не удалось сохранить доступ отделов");
}

export function ServicesAcsSettings() {
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Camera className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Снимки серверов (ACS)
          </h1>
          <div className="text-xs text-dim">
            доступ к внешнему сервису снимков дисков (Clonezilla) · платформа
            + отделы
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-6">
        <AcsSettingsForm />
        <AcsDepartmentTable />
      </div>
    </div>
  );
}

// ── Платформенный singleton ─────────────────────────────────────────────────

function AcsSettingsForm() {
  const toast = useToast();
  const cfgQ = useQuery<AcsSettings>(() => getAcsSettings(), []);
  const loaded = cfgQ.data;

  const [enabled, setEnabled] = useState(false);
  const [acsUrl, setAcsUrl] = useState("");
  const [password, setPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    setEnabled(loaded.enabled);
    setAcsUrl(loaded.acs_url ?? "");
    setPassword("");
    setClearPassword(false);
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return (
      enabled !== loaded.enabled ||
      acsUrl.trim() !== (loaded.acs_url ?? "") ||
      password.trim() !== "" ||
      clearPassword
    );
  }, [loaded, enabled, acsUrl, password, clearPassword]);

  const wouldDisableOnEnable =
    enabled &&
    !password.trim() &&
    !acsUrl.trim() &&
    !(loaded?.password_is_set && loaded?.acs_url);

  function onPasswordChange(v: string) {
    setPassword(v);
    if (v) setClearPassword(false);
  }

  async function handleSave() {
    if (pending || !loaded) return;
    setPending(true);
    try {
      const body: Parameters<typeof updateAcsSettings>[0] = {
        enabled,
        acs_url: acsUrl.trim(),
      };
      if (password) {
        body.acs_password = password;
      } else if (clearPassword) {
        body.clear_password = true;
      }
      await updateAcsSettings(body);
      toast.success("Настройки ACS сохранены");
      cfgQ.refetch();
    } catch (err) {
      toast.error(settingsSaveError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 max-w-md">
      {cfgQ.loading && cfgQ.data == null && (
        <div className="text-xs text-dim text-center py-4">Загрузка…</div>
      )}

      {!cfgQ.loading && cfgQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(cfgQ.error, "Настройки не загрузились")}</div>
            <Button variant="ghost"
              className="mt-2"
              onClick={() => cfgQ.refetch()}
              type="button"
            >
              Повторить
            </Button>
          </div>
        </div>
      )}

      {loaded != null && (
        <>
          <Checkbox
            label="Снимки ACS включены"
            rowClassName="text-sm"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />

          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">URL ACS</span>
            <input
              className="field-input mono"
              value={acsUrl}
              onChange={(e) => setAcsUrl(e.target.value)}
              placeholder="https://acs.example.ru/"
            />
          </label>

          <div className="flex flex-col gap-1">
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">
                Пароль clonezilla-сервера
                <span className="text-dim text-xs ml-1">
                  ({loaded.password_is_set ? "задан" : "не задан"}, write-only)
                </span>
              </span>
              <input
                className="field-input mono"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => onPasswordChange(e.target.value)}
                placeholder={
                  loaded.password_is_set ? "оставьте пустым — не менять" : "не задан"
                }
              />
            </label>
            <Checkbox
              label="Стереть сохранённый пароль"
              rowClassName="text-sm"
              checked={clearPassword}
              disabled={!!password}
              onChange={(e) => setClearPassword(e.target.checked)}
            />
          </div>

          {wouldDisableOnEnable && (
            <div className="alert-warn text-xs flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <div>Нельзя включить ACS без сохранённых URL и пароля.</div>
            </div>
          )}

          <div className="flex items-center gap-3">
            <Button variant="primary"
              type="button"
              onClick={handleSave}
              disabled={pending || !dirty}
            >
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && (
              <span className="text-xs text-dim">
                есть несохранённые изменения
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Per-department opt-in ────────────────────────────────────────────────────

function AcsDepartmentTable() {
  const toast = useToast();
  const accessQ = useQuery<AcsDepartmentAccessItem[]>(
    () => getAcsDepartmentAccess(),
    [],
  );
  const deptsQ = useQuery<Department[]>(() => listDepartments(), []);

  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState(false);

  const initialFlags = useMemo(() => {
    const out: Record<string, boolean> = {};
    for (const item of accessQ.data ?? []) out[item.department_id] = item.is_enabled;
    return out;
  }, [accessQ.data]);

  useEffect(() => {
    setFlags(initialFlags);
  }, [initialFlags]);

  const departments = deptsQ.data ?? [];

  const dirty = useMemo(() => {
    if (accessQ.data == null) return false;
    const ids = new Set([...Object.keys(initialFlags), ...Object.keys(flags)]);
    for (const id of ids) {
      if ((initialFlags[id] ?? false) !== (flags[id] ?? false)) return true;
    }
    return false;
  }, [initialFlags, flags, accessQ.data]);

  function toggle(id: string) {
    setFlags((prev) => ({ ...prev, [id]: !(prev[id] ?? false) }));
  }

  async function handleSave() {
    if (pending || !dirty) return;
    setPending(true);
    try {
      const ids = new Set([...Object.keys(initialFlags), ...Object.keys(flags)]);
      const items = [...ids]
        .filter((id) => (initialFlags[id] ?? false) !== (flags[id] ?? false))
        .map((id) => ({ department_id: id, is_enabled: flags[id] ?? false }));
      if (items.length === 0) return;
      await updateAcsDepartmentAccess(items);
      toast.success("Доступ отделов к снимкам ACS сохранён");
      accessQ.refetch();
    } catch (err) {
      toast.error(departmentsSaveError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 max-w-md">
      <div>
        <div className="field-label">Отделы с доступом к снимкам ACS</div>
        <div className="text-[11px] text-dim">
          per-department opt-in поверх обычных прав server_service
        </div>
      </div>

      {accessQ.loading && accessQ.data == null && (
        <div className="text-xs text-dim py-2">Загрузка…</div>
      )}

      {!accessQ.loading && accessQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(accessQ.error, "Список не загрузился")}</div>
            <Button variant="ghost"
              className="mt-2"
              onClick={() => accessQ.refetch()}
              type="button"
            >
              Повторить
            </Button>
          </div>
        </div>
      )}

      {accessQ.data != null && (
        <>
          {deptsQ.loading && departments.length === 0 && (
            <div className="text-xs text-dim">Загрузка отделов…</div>
          )}
          {departments.length === 0 && !deptsQ.loading ? (
            <div className="text-xs text-dim">Отделов нет.</div>
          ) : (
            <div className="flex flex-col gap-1">
              {departments.map((d) => (
                <Checkbox
                  key={d.id}
                  rowClassName="border border-token rounded px-3 py-1.5"
                  checked={flags[d.id] ?? false}
                  onChange={() => toggle(d.id)}
                  label={<span className="flex-1">{d.name}</span>}
                />
              ))}
            </div>
          )}

          <div className="flex items-center gap-3">
            <Button variant="primary"
              type="button"
              onClick={handleSave}
              disabled={pending || !dirty}
            >
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && (
              <span className="text-xs text-dim">
                есть несохранённые изменения
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
