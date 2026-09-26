/**
 * Справочник семейств статистики (D18) — секция страницы «Пересчёт
 * статистики» (`ServicesStatisticsSettings`).
 *
 * Одна строка — один POST во внешний сервис статистики: маршрут `path`
 * (относительно base URL) и тело `title_statistics` + `set_of_test_types` +
 * необязательные `comparison_list`/`comparison_kernel_list`. Сид — восемь
 * семейств легаси (`allta_front.py:729-880`, `statistics_conf.py`). Всё, что
 * заведено/включено здесь, сразу появляется в модалке «Пересчитать
 * статистику» на страницах прогонов и СТП.
 *
 * Источник истины — `testing_service`:
 *   GET /statistics/categories?include_disabled=true
 *   POST /statistics/categories, PATCH/DELETE /statistics/categories/{id}
 * Запись — `(statistics_settings, *, update)`, как у настроек выше.
 */
import { useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";

import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  createStatisticsCategory,
  deleteStatisticsCategory,
  getStatisticsCategories,
  updateStatisticsCategory,
} from "@/api/testing/statistics";
import type {
  StatisticsCategory,
  StatisticsCategoryCreateRequest,
} from "@/api/testing/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/contexts/ToastContext";

function writeError(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужна роль admin в testing_service или dep_admin отдела).";
    if (e.status === 409) return "Семейство с таким ключом уже есть.";
  }
  return apiErrMsg(e, fallback);
}

/** По строке на элемент, пустые строки пропускаются. */
function parseLines(text: string): string[] {
  return text.split("\n").map((line) => line.trim()).filter(Boolean);
}

/** По строке на сравнение, типы тестов внутри строки — через запятую. */
function parseComparisons(text: string): string[][] {
  return parseLines(text).map((line) => line.split(",").map((item) => item.trim()).filter(Boolean));
}

interface FormState {
  key: string;
  label: string;
  path: string;
  title: string;
  types: string;
  comparisons: string;
  kernels: string;
  enabled: boolean;
  sortOrder: string;
}

function toForm(item?: StatisticsCategory): FormState {
  return {
    key: item?.key ?? "",
    label: item?.label ?? "",
    path: item?.path ?? "/base-statistics",
    title: item?.title_statistics ?? "",
    types: (item?.set_of_test_types ?? []).join("\n"),
    comparisons: (item?.comparison_list ?? []).map((group) => group.join(", ")).join("\n"),
    kernels: (item?.comparison_kernel_list ?? []).join("\n"),
    enabled: item?.enabled ?? true,
    sortOrder: String(item?.sort_order ?? 0),
  };
}

/** Проверка до отправки — те же правила, что у backend-схемы. `null` — всё в порядке. */
function validate(form: FormState, isCreate: boolean): string | null {
  if (isCreate && !/^[a-z][a-z0-9_]*$/.test(form.key.trim())) {
    return "Ключ — латиница в нижнем регистре, цифры и «_», начинается с буквы (например, docker).";
  }
  if (!form.label.trim()) return "Укажите подпись.";
  if (!form.path.trim().startsWith("/") || /\s/.test(form.path.trim())) {
    return "Маршрут начинается с «/» и без пробелов (например, /docker-statistics).";
  }
  if (!form.title.trim()) return "Укажите title_statistics.";
  if (parseLines(form.types).length === 0) return "Нужен хотя бы один тип теста.";
  if (parseComparisons(form.comparisons).some((group) => group.length < 2)) {
    return "В каждом сравнении — минимум два типа теста через запятую.";
  }
  if (!/^\d+$/.test(form.sortOrder.trim())) return "Порядок — целое неотрицательное число.";
  return null;
}

function toRequest(form: FormState): StatisticsCategoryCreateRequest {
  const comparisons = parseComparisons(form.comparisons);
  const kernels = parseLines(form.kernels);
  return {
    key: form.key.trim(),
    label: form.label.trim(),
    path: form.path.trim(),
    title_statistics: form.title.trim(),
    set_of_test_types: parseLines(form.types),
    comparison_list: comparisons.length ? comparisons : null,
    comparison_kernel_list: kernels.length ? kernels : null,
    enabled: form.enabled,
    sort_order: Number(form.sortOrder.trim()),
  };
}

export function StatisticsCategoriesEditor() {
  const toast = useToast();
  const confirm = useConfirm();
  const listQ = useQuery(() => getStatisticsCategories({ includeDisabled: true }), []);
  const items = listQ.data ?? [];
  // "new" — форма добавления, id — правка этой строки, null — только список.
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onDelete(item: StatisticsCategory) {
    if (!item.id) return;
    const ok = await confirm.confirm({
      message: `Удалить семейство «${item.label}» из справочника? Из модалки пересчёта оно пропадёт. Чтобы только скрыть — снимите «Включено».`,
      danger: true,
      confirmLabel: "Удалить",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteStatisticsCategory(item.id);
      toast.success("Семейство удалено");
      listQ.refetch();
    } catch (e) {
      toast.error(writeError(e, "Не удалось удалить семейство"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 max-w-3xl" aria-label="Семейства статистики">
      <div className="flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <h2 className="font-semibold">Семейства статистики</h2>
          <div className="text-xs text-dim">
            Что можно пересчитать по отдельности в модалке «Пересчитать статистику». Каждое
            семейство — один запрос к сервису статистики: маршрут и тело запроса.
          </div>
        </div>
        {editing === null && (
          <Button type="button" className="flex items-center gap-1" onClick={() => setEditing("new")}>
            <Plus className="w-4 h-4" /> Добавить семейство
          </Button>
        )}
      </div>

      {listQ.loading && <div className="text-xs text-dim">Загрузка…</div>}
      {!listQ.loading && !!listQ.error && (
        <div className="alert-danger text-sm">
          {apiErrMsg(listQ.error, "Справочник не загрузился")}
          <Button variant="ghost" className="ml-2" type="button" onClick={() => listQ.refetch()}>Повторить</Button>
        </div>
      )}

      {editing === "new" && (
        <CategoryForm
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            listQ.refetch();
          }}
        />
      )}

      <div className="flex flex-col gap-2">
        {items.map((item) =>
          editing !== null && editing === item.id ? (
            <CategoryForm
              key={item.key}
              existing={item}
              onCancel={() => setEditing(null)}
              onSaved={() => {
                setEditing(null);
                listQ.refetch();
              }}
            />
          ) : (
            <div key={item.key} className="card w-full flex items-start gap-3" data-testid={`statistics-category-${item.key}`}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-sm">{item.label}</span>
                  <span className="mono text-[11px] text-dim">{item.key}</span>
                  {item.enabled === false && <Badge kind="idle">выключено</Badge>}
                </div>
                <div className="mono text-[11px] text-dim truncate">
                  {item.path} · {item.title_statistics} · типов тестов: {item.set_of_test_types?.length ?? 0}
                  {item.comparison_list?.length ? ` · сравнений: ${item.comparison_list.length}` : ""}
                </div>
              </div>
              <div className="flex gap-2 shrink-0">
                <Button
                  size="sm"
                  type="button"
                  className="flex items-center gap-1"
                  disabled={busy || editing !== null}
                  onClick={() => setEditing(item.id ?? null)}
                  aria-label={`Изменить ${item.label}`}
                >
                  <Pencil className="w-3.5 h-3.5" /> Изменить
                </Button>
                <Button
                  size="sm"
                  type="button"
                  variant="danger"
                  className="flex items-center gap-1"
                  disabled={busy || editing !== null}
                  onClick={() => onDelete(item)}
                  aria-label={`Удалить ${item.label}`}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </Button>
              </div>
            </div>
          ),
        )}
      </div>
    </section>
  );
}

function CategoryForm({
  existing,
  onCancel,
  onSaved,
}: {
  existing?: StatisticsCategory;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const isCreate = !existing;
  const [form, setForm] = useState<FormState>(() => toForm(existing));
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function set<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function save() {
    const problem = validate(form, isCreate);
    setErr(problem);
    if (problem || pending) return;
    setPending(true);
    try {
      const body = toRequest(form);
      if (isCreate) {
        await createStatisticsCategory(body);
        toast.success("Семейство добавлено");
      } else if (existing?.id) {
        const { key: _key, ...changes } = body;
        await updateStatisticsCategory(existing.id, changes);
        toast.success("Семейство сохранено");
      }
      onSaved();
    } catch (e) {
      const msg = writeError(e, "Не удалось сохранить семейство");
      setErr(msg);
      toast.error(msg);
    } finally {
      setPending(false);
    }
  }

  return (
    <div role="group" className="card w-full flex flex-col gap-3" aria-label={isCreate ? "Новое семейство" : `Правка ${existing?.label}`}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Ключ</span>
          <input
            className="field-input mono"
            value={form.key}
            disabled={!isCreate}
            onChange={(e) => set("key", e.target.value)}
            placeholder="docker"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Подпись</span>
          <input className="field-input" value={form.label} onChange={(e) => set("label", e.target.value)} placeholder="Docker" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">Маршрут сервиса статистики</span>
          <input className="field-input mono" value={form.path} onChange={(e) => set("path", e.target.value)} placeholder="/docker-statistics" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="field-label">title_statistics</span>
          <input className="field-input" value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Docker" />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        <span className="field-label">Типы тестов (set_of_test_types) — по одному в строке</span>
        <textarea className="field-input mono" rows={4} value={form.types} onChange={(e) => set("types", e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="field-label">Сравнения (comparison_list) — по одному в строке, типы через запятую</span>
        <textarea
          className="field-input mono"
          rows={2}
          value={form.comparisons}
          onChange={(e) => set("comparisons", e.target.value)}
          placeholder="postgresql, postgresql-sm"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="field-label">Сравнение ядер (comparison_kernel_list) — по одному в строке</span>
        <textarea className="field-input mono" rows={1} value={form.kernels} onChange={(e) => set("kernels", e.target.value)} />
      </label>
      <div className="flex items-center gap-4 flex-wrap">
        <Checkbox label="Включено" rowClassName="text-sm" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
        <label className="flex items-center gap-2 text-sm">
          <span className="field-label">Порядок</span>
          <input className="field-input mono w-24" inputMode="numeric" value={form.sortOrder} onChange={(e) => set("sortOrder", e.target.value)} />
        </label>
      </div>
      {err && <div role="alert" className="text-xs text-danger">{err}</div>}
      <div className="flex gap-2">
        <Button type="button" variant="primary" disabled={pending} onClick={save}>
          {pending ? "Сохраняем…" : isCreate ? "Добавить" : "Сохранить"}
        </Button>
        <Button type="button" disabled={pending} onClick={onCancel}>Отмена</Button>
      </div>
    </div>
  );
}
