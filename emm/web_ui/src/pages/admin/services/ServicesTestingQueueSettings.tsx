/**
 * Очередь и повторы тестирования (`/admin/services.testing.queue_settings`).
 *
 * `department_test_settings` — ретрай провалившихся прогонов. Учётка
 * исполнения теста (логин/пароль/SSH-ключ) с живёт на отдельной
 * странице «Тестовая учётка». Расписание HR-отчёта живёт отдельно, на
 * странице отчёта (см. `HrReportCard` на Home) — это два независимых
 * переключателя одной и той же строки в БД, разнесённые по UI, потому что
 * концептуально не связаны.
 *
 * Ниже — отдельный блок `preflight`: проверка внешних сервисов перед
 * запуском теста, см. `_preflightSettingsForm.tsx`. За ним — «Вердикт теста
 * из Zephyr»: ожидание статуса и таблица маппинга,
 * `_zephyrVerdictSettingsForm.tsx`.
 *
 * Источник истины — `testing_service`:
 *   GET/PUT /api/testing/v1/department-test-settings/{department_id}
 * Гейтится тем же кругом, что и «Стенды пула»: department_admin своего
 * отдела или носитель `admin` service-роли testing_service.
 *
 * «Порядок прогона РЦ» — `campaign_sort_rule`: в каком порядке
 * кампания ставит свой состав в очередь стенда. Дефолт — легаси
 * (`allta_back.py:393`): режим → ядро → имя тест-кейса. Одиночные/debug-
 * запуски правилу не подчиняются — FIFO; после постановки порядок меняется
 * перетаскиванием в очереди стенда.
 */

import { useMemo, useState } from "react";
import { AlertCircle, ArrowDown, ArrowUp, ListChecks, Plus, RotateCcw, X } from "lucide-react";

import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getDepartmentTestSettings,
  upsertDepartmentTestSettings,
} from "@/api/testing/departmentTestSettings";
import type {
  CampaignSortKey,
  CampaignSortRuleItem,
  DepartmentTestSettingsUpdateRequest,
} from "@/api/testing/types";
import { Button } from "@/components/ui/Button";
import { Dropdown } from "@/components/ui/Dropdown";
import { Toggle } from "@/components/ui/Toggle";
import { PreflightSettingsForm } from "@/pages/admin/services/_preflightSettingsForm";
import { ZephyrVerdictSettingsForm } from "@/pages/admin/services/_zephyrVerdictSettingsForm";

/** Подписи ключей белого списка `testing_service` (`CampaignSortKey`), в порядке показа. */
const SORT_KEY_LABELS: Record<CampaignSortKey, string> = {
  mode: "Режим (orel/smolensk)",
  kernel: "Ядро",
  test_case_name: "Имя тест-кейса",
  test_code: "Код теста",
  priority: "Приоритет теста",
};
const SORT_KEYS = Object.keys(SORT_KEY_LABELS) as CampaignSortKey[];
const DIRECTION_OPTIONS = [
  { value: "asc", label: "по возрастанию" },
  { value: "desc", label: "по убыванию" },
];

/**
 * То, что сервис отдаёт отделу без своей строки, и то, что сидит миграция —
 * здесь только для кнопки «Сбросить на легаси» и для ответа старого бэкенда
 * без этого поля.
 */
const LEGACY_SORT_RULE: CampaignSortRuleItem[] = [
  { key: "mode", direction: "asc" },
  { key: "kernel", direction: "asc" },
  { key: "test_case_name", direction: "asc" },
];

function sameRule(a: CampaignSortRuleItem[], b: CampaignSortRuleItem[]): boolean {
  return a.length === b.length && a.every((item, i) => item.key === b[i].key && item.direction === b[i].direction);
}

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
  const [sortRule, setSortRule] = useState<CampaignSortRuleItem[]>(LEGACY_SORT_RULE);
  const [pending, setPending] = useState(false);
  const loadedRule = useMemo(() => loaded?.campaign_sort_rule ?? LEGACY_SORT_RULE, [loaded]);

  // Свежие данные с сервера сбрасывают черновик во время рендера, а не в
  // effect: с effect'ом первый кадр после загрузки показывал форму
  // «изменённой», а правка, сделанная до срабатывания effect'а, затиралась.
  const [syncedWith, setSyncedWith] = useState(loaded);
  if (syncedWith !== loaded) {
    setSyncedWith(loaded);
    if (loaded) {
      setRetryEnabled(loaded.retry_enabled);
      setSortRule(loadedRule);
    }
  }

  const ruleDirty = !sameRule(sortRule, loadedRule);
  const dirty = useMemo(() => {
    if (!loaded) return false;
    return retryEnabled !== loaded.retry_enabled || ruleDirty;
  }, [loaded, retryEnabled, ruleDirty]);

  async function handleSave() {
    if (pending || !loaded) return;
    if (sortRule.length === 0) {
      toast.error("В порядке прогона РЦ нужен хотя бы один ключ.");
      return;
    }
    setPending(true);
    try {
      const body: DepartmentTestSettingsUpdateRequest = {
        retry_enabled: retryEnabled,
      };
      if (ruleDirty) body.campaign_sort_rule = sortRule;
      await upsertDepartmentTestSettings(departmentId, body);
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
            <div className="text-xs text-dim">
              Пользователь исполнения теста на стенде (логин, пароль, SSH-ключ) задаётся
              в разделе «Тестовая учётка».
            </div>
          </div>
          <CampaignSortRuleEditor rule={sortRule} onChange={setSortRule} />
          <div className="flex items-center gap-3">
            <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
          </div>
          <PreflightSettingsForm
            departmentId={departmentId}
            loaded={loaded.preflight}
            onSaved={() => settingsQ.refetch()}
          />
          <ZephyrVerdictSettingsForm
            departmentId={departmentId}
            loaded={loaded}
            onSaved={() => settingsQ.refetch()}
          />
          <LiveLogSettings
            departmentId={departmentId}
            interval={loaded.log_chunk_interval_seconds ?? 2.5}
            maxBytes={loaded.log_chunk_max_bytes ?? 4096}
            onSaved={() => settingsQ.refetch()}
          />
        </>
      )}
    </div>
  );
}

/**
 * Упорядоченный список ключей сортировки кампании: первый — самый значимый.
 * Каждый ключ — не больше одного раза (так же валидирует сервис).
 */
function CampaignSortRuleEditor({
  rule,
  onChange,
}: {
  rule: CampaignSortRuleItem[];
  onChange: (rule: CampaignSortRuleItem[]) => void;
}) {
  const used = new Set(rule.map((item) => item.key));
  const freeKeys = SORT_KEYS.filter((key) => !used.has(key));

  function update(index: number, patch: Partial<CampaignSortRuleItem>) {
    onChange(rule.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }
  function move(index: number, delta: -1 | 1) {
    const target = index + delta;
    if (target < 0 || target >= rule.length) return;
    const next = rule.slice();
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  return (
    <section className="flex flex-col gap-2 max-w-2xl" aria-label="Порядок прогона РЦ">
      <div>
        <div className="text-sm font-semibold">Порядок прогона РЦ</div>
        <div className="text-xs text-dim mt-1">
          В каком порядке тесты прогона всей РЦ встают в очередь стенда. Первый ключ — самый значимый, при
          равенстве решает следующий. По умолчанию как в легаси: режим → ядро → имя тест-кейса. Одиночные и
          debug-запуски встают в конец очереди (в порядке постановки), а порядок уже поставленных тестов меняется
          перетаскиванием в очереди стенда.
        </div>
      </div>
      <ol className="grid gap-2">
        {rule.map((item, index) => (
          <li
            key={item.key}
            data-testid="campaign-sort-rule-row"
            className="surface-2 border border-token rounded p-2 grid grid-cols-[24px_1fr_160px_auto] gap-2 items-center"
          >
            <span className="mono text-xs text-dim">{index + 1}</span>
            <Dropdown
              mode="single"
              searchable={false}
              sortOptions={false}
              options={SORT_KEYS.map((key) => ({
                value: key,
                label: SORT_KEY_LABELS[key],
                disabled: key !== item.key && used.has(key),
              }))}
              value={item.key}
              onChange={(value) => update(index, { key: value as CampaignSortKey })}
            />
            <Dropdown
              mode="single"
              searchable={false}
              sortOptions={false}
              options={DIRECTION_OPTIONS}
              value={item.direction}
              onChange={(value) => update(index, { direction: value as CampaignSortRuleItem["direction"] })}
            />
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" aria-label={`Поднять ключ ${index + 1}`} disabled={index === 0} onClick={() => move(index, -1)}>
                <ArrowUp className="w-3.5 h-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Опустить ключ ${index + 1}`}
                disabled={index === rule.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown className="w-3.5 h-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Убрать ключ ${index + 1}`}
                disabled={rule.length <= 1}
                onClick={() => onChange(rule.filter((_, i) => i !== index))}
              >
                <X className="w-3.5 h-3.5" />
              </Button>
            </div>
          </li>
        ))}
      </ol>
      <div className="flex items-center gap-2 flex-wrap">
        <Button
          size="sm"
          className="inline-flex items-center gap-1"
          disabled={freeKeys.length === 0}
          onClick={() => onChange([...rule, { key: freeKeys[0], direction: "asc" }])}
        >
          <Plus className="w-3.5 h-3.5" />
          Добавить ключ
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="inline-flex items-center gap-1"
          disabled={sameRule(rule, LEGACY_SORT_RULE)}
          onClick={() => onChange(LEGACY_SORT_RULE)}
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Как в легаси
        </Button>
      </div>
    </section>
  );
}

/**
 * Живой лог: как часто и какими кусками воркер шлёт вывод теста.
 * Действует со следующего запуска — значения уходят воркеру в задании.
 */
function LiveLogSettings({
  departmentId,
  interval,
  maxBytes,
  onSaved,
}: {
  departmentId: string;
  interval: number;
  maxBytes: number;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [intervalDraft, setIntervalDraft] = useState(String(interval));
  const [bytesDraft, setBytesDraft] = useState(String(maxBytes));
  const [source, setSource] = useState<[number, number]>([interval, maxBytes]);
  const [pending, setPending] = useState(false);
  if (source[0] !== interval || source[1] !== maxBytes) {
    setSource([interval, maxBytes]);
    setIntervalDraft(String(interval));
    setBytesDraft(String(maxBytes));
  }
  const dirty = intervalDraft !== String(interval) || bytesDraft !== String(maxBytes);

  async function save() {
    const i = Number(intervalDraft.replace(",", "."));
    const b = Number(bytesDraft);
    if (!Number.isFinite(i) || i < 0.2 || i > 60) {
      toast.error("Интервал живого лога — от 0.2 до 60 секунд.");
      return;
    }
    if (!Number.isInteger(b) || b < 256 || b > 1048576) {
      toast.error("Размер куска живого лога — целое число байт от 256 до 1048576.");
      return;
    }
    setPending(true);
    try {
      await upsertDepartmentTestSettings(departmentId, { log_chunk_interval_seconds: i, log_chunk_max_bytes: b });
      toast.success("Настройки живого лога сохранены");
      onSaved();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить настройки живого лога"));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="card flex flex-col gap-3 max-w-2xl" aria-label="Живой лог">
      <div className="text-sm font-semibold">Живой лог теста</div>
      <div className="text-xs text-dim">
        Воркер отправляет накопленный вывод теста не реже заданного интервала или сразу, когда кусок достиг
        размера. Действует со следующего запуска.
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Интервал, с</span>
          <input className="field-input mono" value={intervalDraft} onChange={(e) => setIntervalDraft(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Наибольший кусок, байт</span>
          <input className="field-input mono" value={bytesDraft} onChange={(e) => setBytesDraft(e.target.value)} />
        </label>
      </div>
      <div>
        <Button variant="primary" type="button" onClick={save} disabled={pending || !dirty}>
          {pending ? "Сохраняем…" : "Сохранить живой лог"}
        </Button>
      </div>
    </section>
  );
}
