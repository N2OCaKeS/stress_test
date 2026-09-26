/**
 * Настройки внешнего сервиса статистики (`/admin/services.testing.statistics_settings`).
 *
 * Сервис статистики (ветка `statistics` этого же монорепо, `statistics/main_api.py`)
 * живёт отдельно от testing_service — платформенный singleton: кил-свитч
 * `enabled` и `base_url`. Учётка для вызова `/all-statistics` берётся из
 * `department_integration_settings` того отдела, который триггерит пересчёт —
 * здесь настраивается только доступность и адрес самого внешнего сервиса.
 *
 * Источник истины — `testing_service`:
 *   GET/PUT /api/testing/v1/statistics/settings
 * Гейтится матрицей `(statistics_settings, *, update)` — тот же круг, что и
 * «Стенды пула» (department_admin своего отдела или носитель testing.admin).
 *
 * Ниже — справочник семейств для пер-категорийного пересчёта (D18,
 * `StatisticsCategoriesEditor`): то, что выбирается в модалке «Пересчитать
 * статистику» на страницах прогонов и СТП.
 */

import { useEffect, useMemo, useState } from "react";
import { BarChart3, AlertCircle } from "lucide-react";

import { Checkbox } from "@/components/ui/Checkbox";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getStatisticsSettings,
  updateStatisticsSettings,
} from "@/api/testing/statistics";
import type { StatisticsSettings } from "@/api/testing/types";
import { StatisticsCategoriesEditor } from "./StatisticsCategoriesEditor";

function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужна роль admin в testing_service или dep_admin отдела).";
    if (e.status === 422) return "Backend отклонил значения.";
  }
  return apiErrMsg(e, "Не удалось сохранить настройки статистики");
}

export function ServicesStatisticsSettings() {
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <BarChart3 className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Пересчёт статистики
          </h1>
          <div className="text-xs text-dim">
            доступ к внешнему сервису статистики (ветка `statistics`) · платформа
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-6">
        <StatisticsSettingsForm />
        <StatisticsCategoriesEditor />
      </div>
    </div>
  );
}

function StatisticsSettingsForm() {
  const toast = useToast();
  const cfgQ = useQuery<StatisticsSettings>(() => getStatisticsSettings(), []);
  const loaded = cfgQ.data;

  const [enabled, setEnabled] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    setEnabled(loaded.enabled);
    setBaseUrl(loaded.base_url ?? "");
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return enabled !== loaded.enabled || baseUrl.trim() !== (loaded.base_url ?? "");
  }, [loaded, enabled, baseUrl]);

  const wouldEnableWithoutUrl = enabled && !baseUrl.trim();

  async function handleSave() {
    if (pending || !loaded) return;
    setPending(true);
    try {
      await updateStatisticsSettings({ enabled, base_url: baseUrl.trim() });
      toast.success("Настройки статистики сохранены");
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
            <Button variant="ghost" className="mt-2" onClick={() => cfgQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}

      {loaded != null && (
        <>
          <Checkbox
            label="Фоновый пересчёт статистики включён"
            rowClassName="text-sm"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />

          <label className="flex flex-col gap-1 text-sm">
            <span className="field-label">Base URL сервиса статистики</span>
            <input
              className="field-input mono"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://allta.devos.astralinux.ru:7777"
            />
          </label>

          <div className="text-xs text-dim">
            Пересчёт запускается автоматически в конце прогона либо вручную —
            кнопкой «Пересчитать статистику» на страницах прогонов, СТП и
            одиночных тестов, — и всегда выполняется в фоне: не блокирует
            постановку новых тестов в очередь. Учётка Confluence для вызова берётся из настроек
            интеграции того отдела, который триггерит пересчёт.
          </div>

          {wouldEnableWithoutUrl && (
            <div className="alert-warn text-xs">
              Без указанного адреса включённый пересчёт не будет запускаться —
              каждый триггер тихо пропустится.
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
