/**
 * Управление сервисами хоста ALLTA своего отдела
 * (`/admin/services.server.host_control`).
 *
 * Per-department: свой SSH-хост/порт/пользователь/ключ плюс произвольный
 * список systemd-юнитов (unit_name + label), которые страница «Здоровье
 * служб» (`@/pages/health/ServicesHealth`) показывает и умеет
 * start/stop/restart через SSH. Ключ — ссылка на сервисную запись host_ssh
 * своего отдела (та же выпадашка, что и у пароля ACS); значение никогда не
 * возвращается, только факт `private_key_is_set` и, пока старое хранение ещё
 * не убрано, `legacy_private_key_is_set` для предупреждающего баннера.
 *
 * Источник истины — `server_service`, всегда СВОЙ отдел caller'а (без
 * department_id в запросе, резолвится backend'ом из identity):
 *   GET/PUT   /api/server/v1/settings/host-services
 *   GET       /api/server/v1/settings/host-services/credentials
 *   GET/POST  /api/server/v1/settings/host-services/units
 *   PATCH/DELETE /api/server/v1/settings/host-services/units/{unit_id}
 * Гейтится `canManageHostServices` (department_admin своего отдела или
 * роль server_service.admin); каталог (`adminCatalog.ts`) уже фильтрует
 * пункт по этому же условию, здесь — defence-in-depth на случай прямого
 * перехода по URL.
 */

import { useEffect, useMemo, useState } from "react";
import { Terminal, AlertCircle, AlertTriangle, Plus, Trash2, Pencil, Check, X } from "lucide-react";

import { Dropdown } from "@/components/ui/Dropdown";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { usePersona } from "@/contexts/PersonaContext";
import { canManageHostServices } from "@/lib/rbac";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import { formatMskShort } from "@/lib/datetime";
import {
  getHostServicesSettings,
  updateHostServicesSettings,
  listHostSshCredentials,
  listHostServiceUnits,
  createHostServiceUnit,
  renameHostServiceUnit,
  deleteHostServiceUnit,
} from "@/api/server/hostServicesSettings";
import type { HostServiceUnit, HostServicesSettings } from "@/api/server/types";
import { Button } from "@/components/ui/Button";

function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403)
      return "Недостаточно прав (нужен department_admin или server_service.admin своего отдела).";
    if (e.errorCode === "HOST_SSH_KEY_MANAGED_EXTERNALLY")
      return "Ключ управляется сервисом секретов — измените его там.";
    if (e.status === 422) return "Backend отклонил значения (проверьте поля).";
  }
  return apiErrMsg(e, "Не удалось сохранить настройки");
}

function addUnitError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403)
      return "Недостаточно прав (нужен department_admin или server_service.admin своего отдела).";
    if (e.errorCode === "HOST_SERVICE_UNIT_ALREADY_EXISTS")
      return "Такой unit_name уже есть в списке отдела.";
    if (e.errorCode === "HOST_SERVICE_UNIT_NAME_INVALID")
      return "Некорректное имя юнита.";
  }
  return apiErrMsg(e, "Не удалось добавить юнит");
}

function unitMutationError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403)
      return "Недостаточно прав (нужен department_admin или server_service.admin своего отдела).";
    if (e.errorCode === "HOST_SERVICE_UNIT_NOT_FOUND")
      return "Юнит не найден — обновите список.";
  }
  return apiErrMsg(e, "Не удалось выполнить действие");
}

export function ServicesHostControl() {
  const { persona } = usePersona();
  const canManage = canManageHostServices(persona);

  if (!canManage) {
    return (
      <div className="p-8">
        <div className="alert-danger flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          <span>
            403 · управление сервисами хоста доступно department_admin своего
            отдела или роли <span className="mono">server_service.admin</span>.
            account_admin не привязан к отделу и этот раздел не видит.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Terminal className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Управление сервисами хоста
          </h1>
          <div className="text-xs text-dim">
            SSH-доступ и список systemd-юнитов хоста своего отдела · страница
            «Здоровье служб»
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-6">
        <HostControlSettingsForm />
        <HostServiceUnitsSection />
      </div>
    </div>
  );
}

// ── SSH-доступ отдела ────────────────────────────────────────────────────────

function HostControlSettingsForm() {
  const toast = useToast();
  const cfgQ = useQuery<HostServicesSettings>(() => getHostServicesSettings(), []);
  const loaded = cfgQ.data;
  const credentialsQ = useQuery(listHostSshCredentials, []);

  const [sshHost, setSshHost] = useState("");
  const [sshPort, setSshPort] = useState(22);
  const [sshUser, setSshUser] = useState("");
  const [credentialId, setCredentialId] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    setSshHost(loaded.ssh_host ?? "");
    setSshPort(loaded.ssh_port || 22);
    setSshUser(loaded.ssh_user ?? "");
    setCredentialId(loaded.credential_id ?? "");
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return (
      sshHost.trim() !== (loaded.ssh_host ?? "") ||
      sshPort !== (loaded.ssh_port || 22) ||
      sshUser.trim() !== (loaded.ssh_user ?? "") ||
      credentialId.trim() !== (loaded.credential_id ?? "")
    );
  }, [loaded, sshHost, sshPort, sshUser, credentialId]);

  async function handleSave() {
    if (pending || !loaded) return;
    setPending(true);
    try {
      const body: Parameters<typeof updateHostServicesSettings>[0] = {
        ssh_host: sshHost.trim(),
        ssh_port: sshPort,
        ssh_user: sshUser.trim(),
      };
      if (credentialId.trim() !== (loaded.credential_id ?? "")) body.credential_id = credentialId.trim() || null;
      await updateHostServicesSettings(body);
      toast.success("Настройки сохранены");
      cfgQ.refetch();
    } catch (err) {
      toast.error(saveError(err));
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
          <div className="text-xs text-dim">
            {loaded.configured
              ? "SSH-доступ настроен."
              : "SSH-доступ не настроен — управление сервисами хоста недоступно."}
          </div>

          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Хост</span>
            <input
              className="field-input mono"
              value={sshHost}
              onChange={(e) => setSshHost(e.target.value)}
              placeholder="host.example.ru"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Порт</span>
            <input
              className="field-input mono"
              type="number"
              min={1}
              max={65535}
              value={sshPort}
              onChange={(e) => setSshPort(Number(e.target.value) || 22)}
              placeholder="22"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Пользователь</span>
            <input
              className="field-input mono"
              value={sshUser}
              onChange={(e) => setSshUser(e.target.value)}
              placeholder="root"
            />
          </label>

          <div className="flex flex-col gap-1 text-sm">
            <Dropdown mode="single" label="Приватный SSH-ключ" placeholder="Выберите запись" value={credentialId} onChange={setCredentialId}
              disabled={credentialsQ.isFetching} options={[
                ...(credentialId && !credentialsQ.data?.some((item) => item.id === credentialId) ? [{ value: credentialId, label: "Привязанная запись недоступна", disabled: true }] : []),
                ...(credentialsQ.data ?? []).map((item) => ({ value: item.id, label: item.name,
                  disabled: !!((item.valid_from && new Date(item.valid_from).getTime() > Date.now()) || (item.valid_to && new Date(item.valid_to).getTime() < Date.now())),
                })),
              ]} />
            <span className="text-xs text-dim">Создать запись и изменить ключ можно в «Секреты → Сервисные учётные данные», система host_ssh.</span>
            {!!credentialsQ.error && <span role="alert" className="text-xs text-danger">{apiErrMsg(credentialsQ.error, "Не удалось загрузить учётные данные")}</span>}
          </div>
          <div className="text-xs text-dim">Ключ {loaded.private_key_is_set ? "задан" : "не задан"}. При сохранении ссылки проверяется доступ к записи.</div>
          {loaded.legacy_private_key_is_set && <div className="alert-warn text-xs">Используется старое хранение ключа. После выбора проверенной сервисной записи локальная копия будет удалена. Для переноса текущего значения без повторного ввода доступна команда миграции.</div>}

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

// ── Список systemd-юнитов отдела ────────────────────────────────────────────

function HostServiceUnitsSection() {
  const toast = useToast();
  const { confirm } = useConfirm();
  const unitsQ = useQuery(() => listHostServiceUnits(), []);
  const items = unitsQ.data?.items ?? [];

  const [unitName, setUnitName] = useState("");
  const [label, setLabel] = useState("");
  const [adding, setAdding] = useState(false);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (adding) return;
    const name = unitName.trim();
    if (!name) {
      toast.error("Укажите unit_name.");
      return;
    }
    setAdding(true);
    try {
      await createHostServiceUnit({ unit_name: name, label: label.trim() || undefined });
      toast.success(`${name} добавлен`);
      setUnitName("");
      setLabel("");
      unitsQ.refetch();
    } catch (err) {
      toast.error(addUnitError(err));
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(item: HostServiceUnit) {
    if (
      !(await confirm({
        title: "Удалить юнит",
        message: `Убрать ${item.label} (${item.unit_name}) из списка отдела? Управление им со страницы «Здоровье служб» станет недоступно.`,
        confirmLabel: "Удалить",
        danger: true,
      }))
    )
      return;
    try {
      await deleteHostServiceUnit(item.id);
      toast.success(`${item.label} удалён`);
      unitsQ.refetch();
    } catch (err) {
      toast.error(unitMutationError(err));
    }
  }

  async function handleRename(item: HostServiceUnit, newLabel: string) {
    const trimmed = newLabel.trim();
    if (!trimmed || trimmed === item.label) return;
    try {
      await renameHostServiceUnit(item.id, { label: trimmed });
      toast.success("Название обновлено");
      unitsQ.refetch();
    } catch (err) {
      toast.error(unitMutationError(err));
    }
  }

  return (
    <div className="flex flex-col gap-3 max-w-2xl">
      <div>
        <div className="field-label">Systemd-юниты отдела</div>
        <div className="text-[11px] text-dim">
          список юнитов, которые страница «Здоровье служб» проверяет по SSH и
          позволяет start/stop/restart
        </div>
      </div>

      <form onSubmit={handleAdd} className="flex items-end gap-2 flex-wrap">
        <label className="flex flex-col gap-1 text-sm flex-1 min-w-[160px]">
          <span className="field-label">unit_name</span>
          <input
            className="field-input mono"
            value={unitName}
            onChange={(e) => setUnitName(e.target.value)}
            placeholder="allta-worker.service"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm flex-1 min-w-[160px]">
          <span className="field-label">label (опционально)</span>
          <input
            className="field-input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="ALLTA worker"
          />
        </label>
        <Button variant="primary"
          type="submit"
          className="flex items-center gap-1"
          disabled={adding || !unitName.trim()}
        >
          <Plus className="w-4 h-4" />
          {adding ? "Добавляем…" : "Добавить"}
        </Button>
      </form>

      {unitsQ.loading && unitsQ.data == null && (
        <div className="text-xs text-dim text-center py-4">Загрузка…</div>
      )}
      {!unitsQ.loading && unitsQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(unitsQ.error, "Список юнитов не загрузился")}</div>
            <Button variant="ghost"
              className="mt-2"
              onClick={() => unitsQ.refetch()}
              type="button"
            >
              Повторить
            </Button>
          </div>
        </div>
      )}
      {!unitsQ.loading && unitsQ.error == null && items.length === 0 && (
        <div className="text-sm text-dim text-center py-4">
          Юнитов пока нет — добавьте первый выше.
        </div>
      )}
      {!unitsQ.loading && unitsQ.error == null && items.length > 0 && (
        <div className="flex flex-col gap-1">
          {items.map((item) => (
            <UnitRow
              key={item.id}
              item={item}
              onRename={(v) => handleRename(item, v)}
              onDelete={() => handleDelete(item)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function UnitRow({
  item,
  onRename,
  onDelete,
}: {
  item: HostServiceUnit;
  onRename: (label: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.label);

  function startEdit() {
    setDraft(item.label);
    setEditing(true);
  }

  function commit() {
    setEditing(false);
    onRename(draft);
  }

  return (
    <div className="border border-token rounded px-3 py-2 flex items-center gap-2 flex-wrap">
      <div className="flex-1 min-w-[200px]">
        {editing ? (
          <div className="flex items-center gap-1">
            <input
              className="field-input text-sm"
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setEditing(false);
              }}
            />
            <Button variant="ghost" size="sm" type="button" onClick={commit} title="Сохранить">
              <Check className="w-3.5 h-3.5" />
            </Button>
            <Button variant="ghost" size="sm" type="button" onClick={() => setEditing(false)} title="Отмена">
              <X className="w-3.5 h-3.5" />
            </Button>
          </div>
        ) : (
          <div className="text-sm flex items-center gap-1.5">
            {item.label}
            <button
              type="button"
              className="text-dim hover:text-current"
              onClick={startEdit}
              title="Переименовать"
            >
              <Pencil className="w-3 h-3" />
            </button>
          </div>
        )}
        <div className="text-[11px] text-dim mono">
          {item.unit_name} · {formatMskShort(item.created_at)}
        </div>
      </div>
      <Button variant="danger" size="sm"
        type="button"
        className="flex items-center gap-1"
        onClick={onDelete}
        title="Удалить юнит"
      >
        <Trash2 className="w-3.5 h-3.5" /> Удалить
      </Button>
    </div>
  );
}
