/**
 * Шаблоны имени снимка ВМ для тестов (`/admin/services.server.vm_test_snapshots`).
 *
 * Перед тестом на ВМ-стенде server_service откатывает ВМ на снимок, в имени
 * которого версия ОС запуска. Таблицы сопоставления нет (решение T7): снимок
 * ищется по имени, шаблоны перебираются по порядку, версия сравнивается после
 * нормализации (`1710rc52` = `1.7.10.52`). Что на какую версию откатит у
 * конкретной ВМ, видно в «Стенды тестирования» → «Снимки по версиям ОС».
 *
 * Источник истины — `GET/PUT /api/server/v1/settings/vm-test`, account_admin.
 */

import { useState } from "react";
import { AlertCircle, Camera } from "lucide-react";

import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getVmTestSettings,
  putVmTestSettings,
  validateVmSnapshotTemplates,
  type VmTestSettings,
} from "@/api/server/vmTestSettings";
import { Button } from "@/components/ui/Button";

function toText(settings: VmTestSettings): string {
  return settings.snapshot_name_templates.join("\n");
}

function parse(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function saveError(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) return "Недостаточно прав (нужен account_admin).";
  return apiErrMsg(e, "Не удалось сохранить шаблоны");
}

export function ServicesVmTestSnapshots() {
  const toast = useToast();
  const query = useQuery<VmTestSettings>(() => getVmTestSettings(), []);
  const loaded = query.data;
  const [draft, setDraft] = useState<string | null>(null);
  const [syncedFrom, setSyncedFrom] = useState<VmTestSettings | undefined>(undefined);
  const [pending, setPending] = useState(false);

  // Черновик синхронизируется с загруженным значением во время рендера, а не
  // в useEffect: иначе первый кадр после загрузки уже «изменён».
  if (loaded !== syncedFrom) {
    setSyncedFrom(loaded);
    setDraft(loaded ? toText(loaded) : null);
  }

  const templates = draft != null ? parse(draft) : [];
  const error = draft != null ? validateVmSnapshotTemplates(templates) : null;
  const dirty = loaded != null && draft != null && templates.join("\n") !== toText(loaded);

  async function handleSave() {
    if (pending || error) return;
    setPending(true);
    try {
      await putVmTestSettings({ snapshot_name_templates: templates });
      toast.success("Шаблоны снимков ВМ сохранены");
      query.refetch();
    } catch (err) {
      toast.error(saveError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Camera className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Снимки ВМ для тестов</h1>
          <div className="text-xs text-dim">
            как называются снимки, на которые откатывается ВМ-стенд перед тестом
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-4 max-w-2xl">
        {query.loading && <div className="text-xs text-dim py-4">Загрузка…</div>}
        {!query.loading && query.error != null && (
          <div className="alert-danger text-sm flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div className="flex-1">
              <div>{apiErrMsg(query.error, "Настройки не загрузились")}</div>
              <Button variant="ghost" className="mt-2" type="button" onClick={() => query.refetch()}>
                Повторить
              </Button>
            </div>
          </div>
        )}

        {draft != null && (
          <>
            <div className="text-xs text-dim grid gap-1">
              <div>
                Шаблоны по одному в строке, проверяются по порядку — берётся первый снимок, который подошёл.
                Версия в имени сравнивается с версией ОС запуска после нормализации
                (<span className="mono">1710rc52</span> = <span className="mono">1.7.10.52</span>).
                Нет подходящего снимка — тест на ВМ не ставится (<span className="mono">VM_SNAPSHOT_NOT_FOUND</span>).
              </div>
              <div>
                Плейсхолдеры: <span className="mono">{"{version}"}</span> — обязателен, ровно один раз;{" "}
                <span className="mono">{"{mode}"}</span> — режим запуска (orel/smolensk);{" "}
                <span className="mono">{"{hostname}"}</span> — hostname гостя (иначе имя ВМ);{" "}
                <span className="mono">{"{vm_name}"}</span> — имя ВМ.
              </div>
              <div>
                По умолчанию: <span className="mono">{"{version}"}</span> — как в легаси (снимок назван версией),
                затем <span className="mono">{"{version}_{mode}"}</span> — эталоны, которые снимает сборка ВМ.
              </div>
            </div>
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">Шаблоны имени снимка</span>
              <textarea
                className="field-input mono min-h-[120px]"
                aria-label="Шаблоны имени снимка"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
            </label>
            {error && (
              <div className="alert-warn text-xs flex items-start gap-2">
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                <div>{error}</div>
              </div>
            )}
            <div className="flex items-center gap-3">
              <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty || error != null}>
                {pending ? "Сохраняем…" : "Сохранить"}
              </Button>
              {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
