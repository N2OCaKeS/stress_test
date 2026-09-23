/**
 * NOPASSWD sudo для тестовых учёток (`/admin/services.server.account_nopasswd_sudo`).
 *
 * `server_account.has_sudo=True` по умолчанию только добавляет учётку в
 * группу `sudo` — обычная политика Debian/Astra всё равно спрашивает пароль
 * на каждый `sudo`-вызов. Для отделов, где сервер/ВМ — одноразовый управляемый
 * тестовый контур, это можно выключить: включённый флаг заставляет
 * server_worker класть per-user NOPASSWD sudoers-правило при provision/prepare
 * (группу `sudo` целиком это не трогает — расширения привилегий прочих её
 * членов не происходит).
 *
 * Два входа на одну настройку в зависимости от роли:
 *
 * * account_admin — оверсайт: таблица всех отделов, batch-переключение
 *   (`AccountNopasswdSudoDepartmentTable`), тот же паттерн, что у
 *   `ServicesAcsSettings`'s `AcsDepartmentTable`.
 * * department_admin своего отдела или роль `server_service.admin`
 *   (`canManageHostServices`) — self-service: один переключатель на свой
 *   отдел (`AccountNopasswdSudoSelfService`), без department_id в запросе.
 *
 * Источник истины — `server_service`:
 *   GET/PUT /api/server/v1/settings/account-nopasswd-sudo (self-service)
 *   GET/PUT /api/server/v1/settings/account-nopasswd-sudo/departments (оверсайт)
 * Каталог (`adminCatalog.ts`) показывает пункт обеим аудиториям; здесь —
 * defence-in-depth на случай прямого перехода по URL с чужой ролью.
 */

import { useEffect, useMemo, useState } from "react";
import { ShieldOff, AlertCircle, AlertTriangle } from "lucide-react";

import { Checkbox } from "@/components/ui/Checkbox";
import { useToast } from "@/contexts/ToastContext";
import { usePersona } from "@/contexts/PersonaContext";
import { isPlatformWideAdmin, canManageHostServices } from "@/lib/rbac";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getAccountNopasswdSudoSettings,
  updateAccountNopasswdSudoSettings,
  getAccountNopasswdSudoDepartments,
  updateAccountNopasswdSudoDepartments,
  type AccountNopasswdSudoSettings,
  type AccountNopasswdSudoDepartmentItem,
} from "@/api/server/accountNopasswdSudoSettings";
import { listDepartments } from "@/api/auth/departments";
import type { Department } from "@/api/auth/types";
import { Button } from "@/components/ui/Button";

function selfServiceSaveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403)
      return "Недостаточно прав (нужен department_admin или server_service.admin своего отдела).";
  }
  return apiErrMsg(e, "Не удалось сохранить настройку");
}

function departmentsSaveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.status === 422) return "Backend отклонил список отделов.";
  }
  return apiErrMsg(e, "Не удалось сохранить список отделов");
}

export function ServicesAccountNopasswdSudo() {
  const { persona } = usePersona();
  const isOversight = isPlatformWideAdmin(persona);
  const isSelfService = !isOversight && canManageHostServices(persona);

  if (!isOversight && !isSelfService) {
    return (
      <div className="p-8">
        <div className="alert-danger flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          <span>
            403 · настройка доступна account_admin (все отделы) либо
            department_admin своего отдела или роли{" "}
            <span className="mono">server_service.admin</span>.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <ShieldOff className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            NOPASSWD sudo для тестовых учёток
          </h1>
          <div className="text-xs text-dim">
            per-user sudoers-правило для sudo-аккаунтов при provision/prepare ·{" "}
            {isOversight ? "все отделы" : "свой отдел"}
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-6">
        {isOversight ? (
          <AccountNopasswdSudoDepartmentTable />
        ) : (
          <AccountNopasswdSudoSelfService />
        )}
      </div>
    </div>
  );
}

// ── Self-service: свой отдел ────────────────────────────────────────────────

function AccountNopasswdSudoSelfService() {
  const toast = useToast();
  const cfgQ = useQuery<AccountNopasswdSudoSettings>(
    () => getAccountNopasswdSudoSettings(),
    [],
  );
  const loaded = cfgQ.data;
  const [pending, setPending] = useState(false);

  async function handleToggle(next: boolean) {
    if (pending) return;
    setPending(true);
    try {
      await updateAccountNopasswdSudoSettings(next);
      toast.success(
        next
          ? "NOPASSWD sudo включён для отдела"
          : "NOPASSWD sudo выключен для отдела",
      );
      cfgQ.refetch();
    } catch (err) {
      toast.error(selfServiceSaveError(err));
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
            <div>{apiErrMsg(cfgQ.error, "Настройка не загрузилась")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => cfgQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}

      {loaded != null && (
        <>
          <Checkbox
            label="Не спрашивать пароль sudo у тестовых учёток (has_sudo=True)"
            rowClassName="text-sm"
            checked={loaded.is_enabled}
            disabled={pending}
            onChange={(e) => handleToggle(e.target.checked)}
          />
          <div className="text-xs text-dim">
            Пока выключено (дефолт), sudo-аккаунты продолжают спрашивать пароль
            на каждый sudo-вызов — обычная группа <span className="mono">sudo</span>.
            Включение начинает класть per-user NOPASSWD sudoers-правило новым
            provision/prepare-вызовам; существующие учётки подхватят его на
            следующем provision/update/apply, автоматического fan-out'а нет.
          </div>
        </>
      )}
    </div>
  );
}

// ── Оверсайт account_admin: все отделы ──────────────────────────────────────

function AccountNopasswdSudoDepartmentTable() {
  const toast = useToast();
  const accessQ = useQuery<AccountNopasswdSudoDepartmentItem[]>(
    () => getAccountNopasswdSudoDepartments(),
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

  const departments = [...(deptsQ.data ?? [])].sort((a, b) =>
    a.name.localeCompare(b.name, "ru", { numeric: true }),
  );

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
      await updateAccountNopasswdSudoDepartments(items);
      toast.success("Флаги NOPASSWD sudo сохранены");
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
        <div className="field-label">Отделы с включённым NOPASSWD sudo</div>
        <div className="text-[11px] text-dim">
          per-department opt-in; каждый отдел может включить/выключить себе
          сам на своей странице
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
            <Button variant="ghost" className="mt-2" onClick={() => accessQ.refetch()} type="button">
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
            <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && (
              <span className="text-xs text-dim">есть несохранённые изменения</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
