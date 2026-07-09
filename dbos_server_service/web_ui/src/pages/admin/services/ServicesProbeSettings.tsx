/**
 * Настройки проб статуса `server_service`
 * (`/admin/services.server.probe_settings`).
 *
 * Пробы статуса (reachability = ping+ssh, power = ipmi/domstate) снимает
 * server_worker. Частота и вкл/выкл этих проб хранятся в БД, чтобы работать
 * одинаково в docker и k8s. Страница редактирует интервалы и флаги.
 *
 * Источник истины — `GET/PUT /api/server/v1/settings/probes`. Гейтится
 * account_admin; остальным backend режет на 403, поэтому пункт каталога
 * показывается только account_admin (см. adminCatalog).
 */

import { useEffect, useMemo, useState } from "react";
import { Gauge, AlertCircle } from "lucide-react";

import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getProbeSettings,
  putProbeSettings,
  MIN_REACHABILITY_INTERVAL_SECONDS,
  MIN_POWER_INTERVAL_SECONDS,
  type ProbeSettings,
} from "@/api/server/probeSettings";

interface FormState {
  reachability: string;
  power: string;
  reachabilityEnabled: boolean;
  powerEnabled: boolean;
}

function toForm(cfg: ProbeSettings): FormState {
  return {
    reachability: String(cfg.reachability_probe_interval_seconds),
    power: String(cfg.power_probe_interval_seconds),
    reachabilityEnabled: cfg.reachability_probe_enabled,
    powerEnabled: cfg.power_probe_enabled,
  };
}

// Валидация на клиенте: целые числа, нижние границы, power >= reachability.
// Возвращает текст ошибки или null.
function validate(form: FormState): string | null {
  const r = Number(form.reachability);
  const p = Number(form.power);
  if (!Number.isInteger(r) || !Number.isInteger(p)) {
    return "Интервалы должны быть целыми числами секунд.";
  }
  if (r < MIN_REACHABILITY_INTERVAL_SECONDS) {
    return `Интервал доступности не меньше ${MIN_REACHABILITY_INTERVAL_SECONDS} с.`;
  }
  if (p < MIN_POWER_INTERVAL_SECONDS) {
    return `Интервал питания не меньше ${MIN_POWER_INTERVAL_SECONDS} с.`;
  }
  if (p < r) {
    return "Интервал питания не может быть короче интервала доступности.";
  }
  return null;
}

function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.status === 400)
      return "Интервал питания не может быть короче интервала доступности.";
    if (e.status === 422) return "Backend отклонил значения (проверьте границы).";
  }
  return apiErrMsg(e, "Не удалось сохранить настройки");
}

export function ServicesProbeSettings() {
  const toast = useToast();
  const cfgQ = useQuery<ProbeSettings>(() => getProbeSettings(), []);
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
      await putProbeSettings({
        reachability_probe_interval_seconds: Number(form.reachability),
        power_probe_interval_seconds: Number(form.power),
        reachability_probe_enabled: form.reachabilityEnabled,
        power_probe_enabled: form.powerEnabled,
      });
      toast.success("Настройки проб сохранены");
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
        <Gauge className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Проверки статуса
          </h1>
          <div className="text-xs text-dim">
            частота проб доступности (ping+ssh) и питания (ipmi/domstate)
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
              <div>{apiErrMsg(cfgQ.error, "Настройки не загрузились")}</div>
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
            <ProbeGroup
              title="Доступность (ping + ssh)"
              hint={`как часто щупать доступность · минимум ${MIN_REACHABILITY_INTERVAL_SECONDS} с`}
              enabled={form.reachabilityEnabled}
              onToggle={(v) => patch({ reachabilityEnabled: v })}
              interval={form.reachability}
              min={MIN_REACHABILITY_INTERVAL_SECONDS}
              onInterval={(v) => patch({ reachability: v })}
            />

            <ProbeGroup
              title="Питание (ipmi / domstate)"
              hint={`как часто щупать питание · минимум ${MIN_POWER_INTERVAL_SECONDS} с`}
              enabled={form.powerEnabled}
              onToggle={(v) => patch({ powerEnabled: v })}
              interval={form.power}
              min={MIN_POWER_INTERVAL_SECONDS}
              onInterval={(v) => patch({ power: v })}
            />

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

function ProbeGroup({
  title,
  hint,
  enabled,
  onToggle,
  interval,
  min,
  onInterval,
}: {
  title: string;
  hint: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  interval: string;
  min: number;
  onInterval: (v: string) => void;
}) {
  return (
    <div className="border border-token rounded p-4 flex flex-col gap-3 max-w-md">
      <label className="flex items-center gap-2 text-sm font-semibold">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onToggle(e.target.checked)}
        />
        {title}
      </label>
      <div className="text-[11px] text-dim">{hint}</div>
      <label className="flex flex-col gap-1 text-sm">
        <span className="field-label">Интервал, секунды</span>
        <input
          className="field-input mono max-w-[140px]"
          type="number"
          min={min}
          step={1}
          value={interval}
          disabled={!enabled}
          onChange={(e) => onInterval(e.target.value)}
        />
      </label>
    </div>
  );
}
