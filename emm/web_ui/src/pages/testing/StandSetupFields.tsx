/**
 * Блок «Настройка стенда» карточки теста и выбор профиля
 * подготовки.
 *
 * Шаг настройки выполняется при подготовке стенда: доп. параметры ядра
 * дописываются в `GRUB_CMDLINE_LINUX_DEFAULT`, bash-скрипт (подстановки
 * `{{CODE}}` из каталога переменных) — от root или тестовой учётки, затем
 * стенд перезагружается. Пустой блок — шага нет (`stand_setup: null`).
 *
 * Тот же блок — у каждого шага многоступенчатого теста
 * (`TestStepsPanel`); там профиль подготовки не выбирается (он у теста) —
 * без `onProvisioningProfileChange` выпадающий список не показывается.
 */

import { useQuery } from "@/api/auth/useQuery";
import { listProvisioningProfiles } from "@/api/testing/provisioningProfiles";
import { Dropdown } from "@/components/ui/Dropdown";
import type { StandSetupDraft } from "./standSetupDraft";

export function StandSetupFields({
  draft,
  onChange,
  departmentId,
  provisioningProfileId = "",
  onProvisioningProfileChange,
  legend = "Настройка стенда",
}: {
  draft: StandSetupDraft;
  onChange: (next: StandSetupDraft) => void;
  departmentId: string | null;
  provisioningProfileId?: string;
  onProvisioningProfileChange?: (id: string) => void;
  legend?: string;
}) {
  const profilesQ = useQuery(
    async () => (departmentId ? (await listProvisioningProfiles(departmentId)).items : []),
    [departmentId],
    { enabled: !!onProvisioningProfileChange },
  );
  const patch = (changes: Partial<StandSetupDraft>) => onChange({ ...draft, ...changes });

  return (
    <fieldset className="flex flex-col gap-2 border border-token rounded p-2" aria-label="Настройка стенда">
      <legend className="text-dim text-xs px-1">{legend}</legend>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">Доп. параметры ядра (через пробел)</span>
        <input
          aria-label="Доп. параметры ядра"
          className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
          value={draft.params}
          onChange={(e) => patch({ params: e.target.value })}
          placeholder="audit=0"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">Скрипт настройки (bash, подстановки {"{{CODE}}"})</span>
        <textarea
          aria-label="Скрипт настройки стенда"
          className="surface-2 border border-token rounded px-2 py-1 mono text-xs"
          rows={4}
          value={draft.script}
          onChange={(e) => patch({ script: e.target.value })}
        />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">От чьего имени</span>
          <Dropdown
            mode="single" searchable={false} sortOptions={false}
            options={[{ value: "root", label: "root" }, { value: "test_user", label: "Тестовая учётка" }]}
            value={draft.runAs}
            onChange={(v) => patch({ runAs: v as StandSetupDraft["runAs"] })}
          />
        </div>
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Когда</span>
          <Dropdown
            mode="single" searchable={false} sortOptions={false}
            options={[
              { value: "after_boot", label: "После подготовки и перезагрузки" },
              { value: "before_kernel", label: "До смены ядра" },
            ]}
            value={draft.phase}
            onChange={(v) => patch({ phase: v as StandSetupDraft["phase"] })}
          />
        </div>
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Перезагрузить после</span>
          <Dropdown
            mode="single" searchable={false} sortOptions={false}
            options={[
              { value: "auto", label: "Да (по умолчанию)" },
              { value: "yes", label: "Да" },
              { value: "no", label: "Нет" },
            ]}
            value={draft.rebootAfter}
            onChange={(v) => patch({ rebootAfter: v as StandSetupDraft["rebootAfter"] })}
          />
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-dim text-xs">Таймаут скрипта, с</span>
          <input
            className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
            inputMode="numeric"
            value={draft.timeout}
            onChange={(e) => patch({ timeout: e.target.value })}
          />
        </label>
      </div>
      {onProvisioningProfileChange && <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">Профиль подготовки</span>
        <Dropdown
          mode="single"
          placeholder="— профиль отдела по умолчанию —"
          options={[
            { value: "", label: "— профиль отдела по умолчанию —" },
            ...(profilesQ.data ?? []).map((p) => ({
              value: p.id,
              label: `${p.name}${p.department_id ? "" : " (общий)"}${p.is_default ? " · по умолчанию" : ""}`,
            })),
          ]}
          value={provisioningProfileId}
          onChange={onProvisioningProfileChange}
        />
      </label>}
    </fieldset>
  );
}
