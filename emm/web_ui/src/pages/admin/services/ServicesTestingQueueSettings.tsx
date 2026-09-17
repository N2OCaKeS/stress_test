/**
 * Очередь и повторы тестирования (`/admin/services.testing.queue_settings`).
 *
 * `department_test_settings` — ретрай провалившихся прогонов и имя учётки
 * исполнения теста на стенде. Расписание HR-отчёта живёт отдельно, на
 * странице отчёта (см. `HrReportCard` на Home) — это два независимых
 * переключателя одной и той же строки в БД, разнесённые по UI, потому что
 * концептуально не связаны.
 *
 * Источник истины — `testing_service`:
 *   GET/PUT /api/testing/v1/department-test-settings/{department_id}
 * Гейтится тем же кругом, что и «Стенды пула»: department_admin своего
 * отдела или носитель `admin` service-роли testing_service.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ListChecks } from "lucide-react";

import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getDepartmentTestSettings,
  upsertDepartmentTestSettings,
} from "@/api/testing/departmentTestSettings";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/Toggle";

export function ServicesTestingQueueSettings() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <ListChecks className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Очередь и повторы тестирования</h1>
          <div className="text-xs text-dim">testing_service · настройки отдела</div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {departmentId ? (
          <QueueSettingsForm departmentId={departmentId} />
        ) : (
          <div className="text-sm text-dim">Нет привязанного отдела.</div>
        )}
      </div>
    </div>
  );
}

function QueueSettingsForm({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const settingsQ = useQuery(() => getDepartmentTestSettings(departmentId), [departmentId]);
  const loaded = settingsQ.data;
  const [retryEnabled, setRetryEnabled] = useState(true);
  const [testUsername, setTestUsername] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    setRetryEnabled(loaded.retry_enabled);
    setTestUsername(loaded.test_username);
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return retryEnabled !== loaded.retry_enabled || testUsername.trim() !== loaded.test_username;
  }, [loaded, retryEnabled, testUsername]);

  async function handleSave() {
    if (pending || !loaded) return;
    const username = testUsername.trim();
    if (!username) {
      toast.error("Укажите имя пользователя исполнения теста.");
      return;
    }
    setPending(true);
    try {
      await upsertDepartmentTestSettings(departmentId, {
        retry_enabled: retryEnabled,
        test_username: username,
      });
      toast.success("Настройки очереди тестирования сохранены");
      settingsQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить настройки очереди"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {settingsQ.loading && !loaded && <div className="text-xs text-dim py-2">Загрузка…</div>}
      {!settingsQ.loading && settingsQ.error != null && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(settingsQ.error, "Настройки не загрузились")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => settingsQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}

      {loaded && (
        <>
          <div className="flex flex-col gap-3 max-w-md">
            <Toggle
              label="Повторять провалившиеся тесты в прогоне"
              checked={retryEnabled}
              onChange={(e) => setRetryEnabled(e.target.checked)}
            />
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">Пользователь исполнения теста на стенде</span>
              <input
                className="field-input mono"
                value={testUsername}
                onChange={(e) => setTestUsername(e.target.value)}
                placeholder="u"
              />
            </label>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
          </div>
        </>
      )}
    </div>
  );
}
