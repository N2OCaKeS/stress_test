/**
 * Шаблоны имени снимка ВМ для подготовки ВМ-стенда под тест.
 *
 * Перед тестом server_service откатывает ВМ на снимок, в имени которого версия
 * ОС запуска (решение T7: снимок ищется по имени, таблицы сопоставления нет).
 * Схема имени — настройка: шаблоны по порядку, первый подошедший снимок берётся.
 *
 * Источник истины — `server_service`:
 *   GET /api/server/v1/settings/vm-test
 *   PUT /api/server/v1/settings/vm-test
 * Гейтится account_admin; остальным backend ответит 403.
 */

import { apiGet, apiPut } from "@/api/client";

/** Плейсхолдеры шаблона (совпадают с backend'ом). `{version}` обязателен, ровно один раз. */
export const VM_SNAPSHOT_TEMPLATE_PLACEHOLDERS = ["version", "mode", "hostname", "vm_name"] as const;
export const MAX_VM_SNAPSHOT_TEMPLATES = 10;

export interface VmTestSettings {
  /** Шаблоны имени снимка ВМ, по порядку. */
  snapshot_name_templates: string[];
}

const BASE = "/server/v1";

export function getVmTestSettings(): Promise<VmTestSettings> {
  return apiGet<VmTestSettings>(`${BASE}/settings/vm-test`);
}

/** Полная замена списка шаблонов. 422 `VM_SNAPSHOT_TEMPLATE_INVALID` — шаблон не прошёл проверку. */
export function putVmTestSettings(body: VmTestSettings): Promise<VmTestSettings> {
  return apiPut<VmTestSettings>(`${BASE}/settings/vm-test`, body);
}

/** Клиентская проверка шаблонов — та же, что на backend'е. Текст ошибки или null. */
export function validateVmSnapshotTemplates(templates: string[]): string | null {
  if (!templates.length) return "Нужен хотя бы один шаблон.";
  if (templates.length > MAX_VM_SNAPSHOT_TEMPLATES) return `Не больше ${MAX_VM_SNAPSHOT_TEMPLATES} шаблонов.`;
  if (new Set(templates).size !== templates.length) return "Шаблоны повторяются.";
  const allowed = new Set<string>(VM_SNAPSHOT_TEMPLATE_PLACEHOLDERS);
  for (const template of templates) {
    const names = [...template.matchAll(/\{([a-z_]+)\}/g)].map((m) => m[1]);
    const unknown = names.filter((name) => !allowed.has(name));
    if (unknown.length) return `«${template}»: неизвестные плейсхолдеры ${unknown.join(", ")}.`;
    if (names.filter((name) => name === "version").length !== 1) {
      return `«${template}»: {version} должен встречаться ровно один раз.`;
    }
  }
  return null;
}
