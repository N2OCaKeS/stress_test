/**
 * Настройки SSH-доступа к хосту ALLTA (`/admin/services.server.host_control`).
 *
 * Платформенный singleton, использует его страница «Здоровье служб»
 * (`@/pages/health/ServicesHealth`) для проверки статуса и старта/стопа/
 * рестарта systemd-юнитов на хосте. Приватный ключ write-only — GET отдаёт
 * только факт `private_key_is_set`, значение никогда не возвращается.
 *
 * Источник истины — `server_service`:
 *   GET/PUT /api/server/v1/settings/host-services
 * Гейтится account_admin; остальным backend режет на 403, поэтому пункт
 * каталога показывается только account_admin (см. adminCatalog).
 */

import { useEffect, useMemo, useState } from "react";
import { Terminal, AlertCircle } from "lucide-react";

import { Checkbox } from "@/components/ui/Checkbox";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getHostServicesSettings,
  updateHostServicesSettings,
  type HostServicesSettings,
} from "@/api/server/hostServicesSettings";
import { Button } from "@/components/ui/Button";

function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.status === 422) return "Backend отклонил значения (проверьте поля).";
  }
  return apiErrMsg(e, "Не удалось сохранить настройки");
}

export function ServicesHostControl() {
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Terminal className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Управление сервисами хоста
          </h1>
          <div className="text-xs text-dim">
            SSH-доступ к хосту ALLTA · нужен для старта/стопа/рестарта
            systemd-юнитов со страницы «Здоровье служб»
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-6">
        <HostControlSettingsForm />
      </div>
    </div>
  );
}

function HostControlSettingsForm() {
  const toast = useToast();
  const cfgQ = useQuery<HostServicesSettings>(() => getHostServicesSettings(), []);
  const loaded = cfgQ.data;

  const [sshHost, setSshHost] = useState("");
  const [sshPort, setSshPort] = useState(22);
  const [sshUser, setSshUser] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [clearPrivateKey, setClearPrivateKey] = useState(false);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    setSshHost(loaded.ssh_host ?? "");
    setSshPort(loaded.ssh_port || 22);
    setSshUser(loaded.ssh_user ?? "");
    setPrivateKey("");
    setClearPrivateKey(false);
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return (
      sshHost.trim() !== (loaded.ssh_host ?? "") ||
      sshPort !== (loaded.ssh_port || 22) ||
      sshUser.trim() !== (loaded.ssh_user ?? "") ||
      privateKey.trim() !== "" ||
      clearPrivateKey
    );
  }, [loaded, sshHost, sshPort, sshUser, privateKey, clearPrivateKey]);

  function onPrivateKeyChange(v: string) {
    setPrivateKey(v);
    if (v) setClearPrivateKey(false);
  }

  async function handleSave() {
    if (pending || !loaded) return;
    setPending(true);
    try {
      const body: Parameters<typeof updateHostServicesSettings>[0] = {
        ssh_host: sshHost.trim(),
        ssh_port: sshPort,
        ssh_user: sshUser.trim(),
      };
      if (privateKey) {
        body.ssh_private_key = privateKey;
      } else if (clearPrivateKey) {
        body.clear_private_key = true;
      }
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

          <div className="flex flex-col gap-1">
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">
                Приватный ключ
                <span className="text-dim text-xs ml-1">
                  ({loaded.private_key_is_set ? "задан" : "не задан"}, write-only)
                </span>
              </span>
              <textarea
                className="field-input mono"
                rows={8}
                autoComplete="off"
                spellCheck={false}
                value={privateKey}
                onChange={(e) => onPrivateKeyChange(e.target.value)}
                placeholder={
                  loaded.private_key_is_set
                    ? "оставьте пустым — не менять"
                    : "-----BEGIN OPENSSH PRIVATE KEY-----"
                }
              />
            </label>
            <Checkbox
              label="Стереть сохранённый ключ"
              rowClassName="text-sm"
              checked={clearPrivateKey}
              disabled={!!privateKey}
              onChange={(e) => setClearPrivateKey(e.target.checked)}
            />
          </div>

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
