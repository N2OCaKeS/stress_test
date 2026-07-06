/**
 * Настраиваемая кнопка левой панели (`/admin/services.nav_link`).
 *
 * account_admin задаёт подпись, внешний URL, флаг включения и правило
 * видимости кнопки (по умолчанию «allta»): либо всем отделам, либо явному
 * списку. Источник истины — `GET/PUT /api/auth/v1/admin/nav-links`. Обычный
 * пользователь видит кнопку под пунктом «ОС», только если его отдел попадает
 * под правило видимости.
 */

import { useEffect, useMemo, useState } from "react";
import { ExternalLink, AlertCircle } from "lucide-react";

import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getNavLinksConfig,
  updateNavLinksConfig,
  type NavLinkConfig,
} from "@/api/auth/navLinks";
import { listDepartments } from "@/api/auth/departments";
import type { Department } from "@/api/auth/types";

function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.errorCode === "NAV_LINK_URL_REQUIRED")
      return "Укажите URL — кнопка включена.";
    if (e.status === 422)
      return "Backend отклонил конфиг (проверьте URL: нужен http/https).";
  }
  return apiErrMsg(e, "Не удалось сохранить конфиг");
}

export function ServicesNavLink() {
  const toast = useToast();
  const cfgQ = useQuery<NavLinkConfig>(() => getNavLinksConfig(), []);
  const deptsQ = useQuery<Department[]>(() => listDepartments(), []);

  const loaded = cfgQ.data;

  const [enabled, setEnabled] = useState(false);
  const [label, setLabel] = useState("allta");
  const [url, setUrl] = useState("");
  const [allDepartments, setAllDepartments] = useState(false);
  const [departmentIds, setDepartmentIds] = useState<string[]>([]);
  const [pending, setPending] = useState(false);

  // Перезаливаем форму, когда приходит/обновляется конфиг.
  useEffect(() => {
    if (!loaded) return;
    setEnabled(loaded.enabled);
    setLabel(loaded.label ?? "allta");
    setUrl(loaded.url ?? "");
    setAllDepartments(loaded.all_departments);
    setDepartmentIds([...(loaded.department_ids ?? [])]);
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return (
      enabled !== loaded.enabled ||
      label.trim() !== (loaded.label ?? "") ||
      url.trim() !== (loaded.url ?? "") ||
      allDepartments !== loaded.all_departments ||
      JSON.stringify([...departmentIds].sort()) !==
        JSON.stringify([...(loaded.department_ids ?? [])].sort())
    );
  }, [loaded, enabled, label, url, allDepartments, departmentIds]);

  function toggleDept(id: string) {
    setDepartmentIds((prev) =>
      prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id],
    );
  }

  async function handleSave() {
    if (pending) return;
    const trimmedUrl = url.trim();
    if (enabled && !trimmedUrl) {
      toast.error("Укажите URL — кнопка включена.");
      return;
    }
    setPending(true);
    try {
      await updateNavLinksConfig({
        enabled,
        label: label.trim() || "allta",
        url: trimmedUrl || null,
        all_departments: allDepartments,
        department_ids: allDepartments ? [] : departmentIds,
      });
      toast.success("Конфиг кнопки сохранён");
      cfgQ.refetch();
    } catch (err) {
      toast.error(saveError(err));
    } finally {
      setPending(false);
    }
  }

  const departments = deptsQ.data ?? [];

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <ExternalLink className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Кнопка «allta»</h1>
          <div className="text-xs text-dim">
            настраиваемая кнопка левой панели · внешняя ссылка · видимость по отделам
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
              <div>{apiErrMsg(cfgQ.error, "Конфиг не загрузился")}</div>
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

        {cfgQ.data != null && (
          <>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>Кнопка включена</span>
            </label>

            <label className="flex flex-col gap-1 text-sm max-w-md">
              <span className="field-label">Подпись</span>
              <input
                className="field-input"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="allta"
              />
            </label>

            <label className="flex flex-col gap-1 text-sm max-w-md">
              <span className="field-label">URL (внешняя ссылка)</span>
              <input
                className="field-input mono"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://allta.example.ru/"
              />
              <span className="text-[11px] text-dim">
                Открывается в новой вкладке. Только http/https.
              </span>
            </label>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={allDepartments}
                onChange={(e) => setAllDepartments(e.target.checked)}
              />
              <span>Показывать всем отделам</span>
            </label>

            {!allDepartments && (
              <div className="flex flex-col gap-2 max-w-md">
                <div className="field-label">Отделы, которым видна кнопка</div>
                {deptsQ.loading && departments.length === 0 && (
                  <div className="text-xs text-dim">Загрузка отделов…</div>
                )}
                {departments.length === 0 && !deptsQ.loading ? (
                  <div className="text-xs text-dim">Отделов нет.</div>
                ) : (
                  <div className="flex flex-col gap-1">
                    {departments.map((d) => (
                      <label
                        key={d.id}
                        className="flex items-center gap-2 text-sm border border-token rounded px-3 py-1.5"
                      >
                        <input
                          type="checkbox"
                          checked={departmentIds.includes(d.id)}
                          onChange={() => toggleDept(d.id)}
                        />
                        <span className="flex-1">{d.name}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center gap-3">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSave}
                disabled={pending || !dirty}
              >
                {pending ? "Сохраняем…" : "Сохранить"}
              </button>
              {dirty && !pending && (
                <span className="text-xs text-dim">есть несохранённые изменения</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
