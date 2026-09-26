/**
 * Модалка «Пересчитать статистику» (решение D18) — кнопка на страницах
 * прогонов и СТП.
 *
 * Автоматический пересчёт по завершении кампании остаётся как был (полный,
 * `services/queue.py`); здесь — ручной запуск: «Вся статистика» (внешний
 * `/all-statistics`) либо выбранные семейства тестов. Список семейств —
 * справочник в БД (`GET /statistics/categories`), редактируется в настройках
 * статистики: новое семейство появляется здесь без правки кода.
 *
 * Несколько семейств уходят одним запросом `categories: [...]` — backend
 * считает их по очереди одной фоновой задачей, так что индикатор статуса
 * (singleton) не перетирается параллельными попытками.
 *
 * Права — как у существующего ручного пересчёта: `(statistics_settings, *,
 * update)`; без них backend ответит 403 и это покажется тостом. Статус
 * последнего пересчёта (`GET /statistics/status`) опрашивается, пока модалка
 * открыта.
 */
import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";

import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getStatisticsCategories,
  getStatisticsStatus,
  triggerStatisticsRecalc,
} from "@/api/testing/statistics";
import type { StatisticsCategory, StatisticsRecalcStatus } from "@/api/testing/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/contexts/ToastContext";
import { formatMsk } from "@/lib/datetime";

const STATUS_POLL_INTERVAL_MS = 5_000;

const STATUS_META: Record<
  StatisticsRecalcStatus["status"],
  { label: string; badge: "idle" | "accent" | "ok" | "danger" }
> = {
  idle: { label: "не запускался", badge: "idle" },
  running: { label: "выполняется", badge: "accent" },
  succeeded: { label: "успешно", badge: "ok" },
  failed: { label: "ошибка", badge: "danger" },
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: "вручную",
  test_run: "по завершении прогона",
};

/** Подписи семейств из справочника; ключ, которого уже нет в справочнике, — как есть. */
function scopeLabel(status: StatisticsRecalcStatus, categories: StatisticsCategory[]): string {
  const keys = status.categories?.length ? status.categories : status.category ? [status.category] : [];
  if (keys.length === 0) return "вся статистика";
  return keys.map((key) => categories.find((item) => item.key === key)?.label ?? key).join(", ");
}

export function StatisticsRecalcButton({ className = "" }: { className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        type="button"
        className={`w-full flex items-center justify-center gap-2 ${className}`.trim()}
        onClick={() => setOpen(true)}
        title="Выбрать, какую статистику пересчитать (в фоне, не блокирует очередь)"
      >
        <BarChart3 className="w-3.5 h-3.5" />
        Пересчитать статистику…
      </Button>
      {open && <StatisticsRecalcModal onClose={() => setOpen(false)} />}
    </>
  );
}

export function StatisticsRecalcModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const categoriesQ = useQuery(() => getStatisticsCategories(), []);
  const categories = categoriesQ.data ?? [];

  const [all, setAll] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<StatisticsRecalcStatus | null>(null);
  const [pollTick, setPollTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const next = await getStatisticsStatus();
        if (!cancelled) setStatus(next);
      } catch {
        // best-effort индикатор — при сбое опроса оставляем последнее значение
      }
    }
    poll();
    const timer = setInterval(poll, STATUS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pollTick]);

  function toggleCategory(key: string, checked: boolean) {
    const next = new Set(selected);
    if (checked) next.add(key);
    else next.delete(key);
    setSelected(next);
    setAll(next.size === 0);
  }

  function toggleAll(checked: boolean) {
    setAll(checked);
    if (checked) setSelected(new Set());
  }

  const chosen = categories.filter((item) => selected.has(item.key));
  const canSubmit = !pending && (all || chosen.length > 0);

  async function submit() {
    if (!canSubmit) return;
    setPending(true);
    try {
      await triggerStatisticsRecalc(all ? {} : { categories: chosen.map((item) => item.key) });
      const scope = all ? "всей статистики" : chosen.map((item) => `«${item.label}»`).join(", ");
      toast.success(`Пересчёт ${scope} запущен в фоне`);
      setPollTick((tick) => tick + 1);
    } catch (error) {
      toast.error(apiErrMsg(error, "Не удалось запустить пересчёт статистики"));
    } finally {
      setPending(false);
    }
  }

  const meta = status ? STATUS_META[status.status] ?? STATUS_META.idle : null;

  return (
    <Modal
      open
      onOpenChange={(next) => !next && onClose()}
      title="Пересчитать статистику"
      subtitle="в фоне, очередь тестов не блокируется"
      icon={<BarChart3 className="w-5 h-5 text-accent" />}
      width="md"
      footer={
        <>
          <Button type="button" onClick={onClose}>Закрыть</Button>
          <Button type="button" variant="primary" disabled={!canSubmit} onClick={submit}>
            {pending ? "Запускаем…" : "Пересчитать"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <section aria-label="Статус последнего пересчёта" className="surface-2 border border-token rounded p-3 text-sm flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold">Последний пересчёт:</span>
            {meta ? <Badge kind={meta.badge}>{meta.label}</Badge> : <span className="text-dim">загрузка…</span>}
          </div>
          {status && status.status !== "idle" && (
            <>
              <div className="text-xs text-dim">
                Объём: {scopeLabel(status, categories)}
                {status.triggered_by ? ` · ${TRIGGER_LABELS[status.triggered_by] ?? status.triggered_by}` : ""}
              </div>
              {status.status === "running" && status.categories && status.categories.length > 1 && status.category && (
                <div className="text-xs text-dim">
                  Сейчас считается: {categories.find((item) => item.key === status.category)?.label ?? status.category}
                </div>
              )}
              <div className="text-xs text-dim">
                Начат: {formatMsk(status.started_at)}
                {status.finished_at ? ` · завершён: ${formatMsk(status.finished_at)}` : ""}
              </div>
              {status.error && <div role="alert" className="text-xs text-danger">{status.error}</div>}
            </>
          )}
        </section>

        {status?.status === "running" && (
          <div className="alert-warn text-xs">
            Пересчёт уже идёт. Новый запуск пойдёт параллельно, индикатор покажет последний из них.
          </div>
        )}

        <div className="flex flex-col gap-2">
          <Checkbox
            label={<span className="font-semibold">Вся статистика</span>}
            rowClassName="text-sm"
            checked={all}
            onChange={(e) => toggleAll(e.target.checked)}
          />
          {categoriesQ.loading && <div className="text-xs text-dim">Загружаем семейства тестов…</div>}
          {!!categoriesQ.error && (
            <div className="text-xs text-danger">{apiErrMsg(categoriesQ.error, "Не удалось загрузить семейства тестов")}</div>
          )}
          {categories.length > 0 && (
            <fieldset className="grid grid-cols-1 sm:grid-cols-2 gap-1 pl-5">
              <legend className="text-xs text-dim mb-1">или отдельные семейства:</legend>
              {categories.map((item) => (
                <Checkbox
                  key={item.key}
                  label={item.label}
                  rowClassName="text-sm"
                  checked={selected.has(item.key)}
                  onChange={(e) => toggleCategory(item.key, e.target.checked)}
                />
              ))}
            </fieldset>
          )}
          {!categoriesQ.loading && !categoriesQ.error && categories.length === 0 && (
            <div className="text-xs text-dim">
              Отдельных семейств нет — их заводят в настройках статистики.
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
