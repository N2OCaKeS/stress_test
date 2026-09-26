/**
 * Ссылки источников глобальных переменных (`source_ref`) и
 * шаблоны `{CODE}` — чистые функции формы переменной. Компоненты —
 * `GlobalVariablesEditor.tsx` и `TemplateInput.tsx`.
 */
import type {
  GlobalVariableSource,
  GlobalVariableSourceOptions,
  GlobalVariableSourceRef,
  GlobalVariableValueType,
} from "@/api/testing/types";

// ── шаблоны {CODE} ────────────────────────────────────────────────────────

// Формат кода — как у сервиса (`variable_resolver._PLACEHOLDER_RE`): всё
// остальное в фигурных скобках остаётся текстом.
export const PLACEHOLDER_RE = /\{([A-Z][A-Z0-9_]*)\}/g;

/** Коды переменных, на которые ссылается шаблон, в порядке появления. */
export function templateCodes(text: string | null | undefined): string[] {
  if (!text) return [];
  return Array.from(text.matchAll(PLACEHOLDER_RE), (m) => m[1]);
}

/** Коды шаблона, которых нет в каталоге. */
export function unknownTemplateCodes(text: string | null | undefined, known: readonly string[]): string[] {
  const set = new Set(known);
  return Array.from(new Set(templateCodes(text).filter((code) => !set.has(code))));
}

// ── source_ref ────────────────────────────────────────────────────────────

/** Подписи источников. Неизвестный сервису UI источник показывается кодом. */
export const SOURCE_LABELS: Record<GlobalVariableSource, string> = {
  launch_context: "launch_context — из контекста запуска (RC, KERNEL, MODE)",
  static: "static — фиксированное значение",
  per_test_override: "per_test_override — задаётся слотом теста",
  secret_service: "secret_service — из контекста запуска (устаревший)",
  template: "template — шаблон из других переменных",
  test_field: "test_field — поле теста",
  stand: "stand — поле стенда",
  department_integration: "department_integration — интеграции отдела стенда",
  os_version: "os_version — карточка версии ОС (РЦ)",
  test_account: "test_account — тестовая учётка отдела",
  zephyr_folder: "zephyr_folder — папка Zephyr отдела и РЦ",
  stand_ref: "stand_ref — поле конкретного стенда (адрес второго стенда)",
};

/** Источники без `source_ref`. */
export const NO_REF_SOURCES = new Set(["launch_context", "per_test_override", "secret_service"]);
/** Источники `{field, fallback?}`. */
export const FIELD_FALLBACK_SOURCES = new Set(["test_field", "stand"]);
/** Источники `{field}`. */
export const FIELD_ONLY_SOURCES = new Set(["test_account", "zephyr_folder"]);

export const VALUE_TYPE_OPTIONS: { value: GlobalVariableValueType; label: string }[] = [
  { value: "string", label: "string" },
  { value: "integer", label: "integer" },
  { value: "boolean", label: "boolean" },
];

export const WHEN_LABELS: Record<string, string> = {
  debug: "только в debug-запуске",
  not_debug: "только в обычном запуске",
};

export const FIELD_HINTS: Record<string, string> = {
  number: "цифры из имени стенда (stand3 → 3)",
  host: "адрес стенда из server_service",
  legacy_token: "имя стенда (stand3)",
  id: "внутренний id стенда",
  name: "имя версии (1.8.1.6); segments — первые N сегментов",
  rc_number: "номер РЦ (RC3)",
  is_urgent_update: "true/false — срочное обновление",
  home: "домашний каталог по шаблону учётки",
  folder_tree_id: "id папки Zephyr (-fti)",
  folder_path: "путь папки Zephyr",
};

/** Ошибки сервиса, которые показываются у поля формы, а не всплывающим сообщением. */
export const FIELD_ERROR_CODES = new Set([
  "VARIABLE_SOURCE_REF_INVALID",
  "VARIABLE_TEMPLATE_UNKNOWN",
  "VARIABLE_TEMPLATE_CYCLE",
  "GLOBAL_VARIABLE_IN_USE",
  "GLOBAL_VARIABLE_DUPLICATE",
]);

export function fieldOptions(fields: string[], withEmpty?: string) {
  const options = fields.map((f) => ({ value: f, label: FIELD_HINTS[f] ? `${f} — ${FIELD_HINTS[f]}` : f }));
  return withEmpty !== undefined ? [{ value: "", label: withEmpty }, ...options] : options;
}

export function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function numOrEmpty(value: unknown): string {
  return typeof value === "number" ? String(value) : "";
}

/** Нормализованный `source_ref` для отправки: только ключи, которые принимает источник. */
export function buildSourceRef(
  source: string,
  draft: GlobalVariableSourceRef,
  options?: GlobalVariableSourceOptions | null,
): GlobalVariableSourceRef | null {
  if (NO_REF_SOURCES.has(source)) return null;
  if (source === "static") {
    const value = str(draft.value);
    return value === "" ? null : { value };
  }
  if (source === "template") {
    const when = str(draft.when);
    return { template: str(draft.template), ...(when ? { when } : {}) };
  }
  const field = str(draft.field);
  if (FIELD_ONLY_SOURCES.has(source)) return { field };
  if (source === "stand_ref") return { stand_id: str(draft.stand_id), field };
  if (FIELD_FALLBACK_SOURCES.has(source)) {
    const fallback = str(draft.fallback);
    return { field, ...(fallback ? { fallback } : {}) };
  }
  if (source === "department_integration") {
    const fallback = str(draft.fallback);
    const isCredential = options?.department_integration_fields.find((f) => f.field === field)?.is_credential
      ?? (field === "credential_id" || field.endsWith("_credential_id"));
    return {
      field,
      ...(fallback ? { fallback } : {}),
      ...(isCredential ? { credential_part: str(draft.credential_part) || null } : {}),
    };
  }
  if (source === "os_version") {
    const segments = str(draft.segments).trim();
    const uuSegments = str(draft.uu_segments).trim();
    return {
      field,
      segments: segments ? Number(segments) : null,
      uu_segments: uuSegments ? Number(uuSegments) : null,
    };
  }
  // Источник, которого UI не знает, — ссылка как есть.
  return draft;
}

/** Черновик формы из сохранённой ссылки: числа — строками для полей ввода. */
export function draftFromRef(ref: GlobalVariableSourceRef | null | undefined): GlobalVariableSourceRef {
  if (!ref) return {};
  const draft: GlobalVariableSourceRef = { ...ref };
  for (const key of ["segments", "uu_segments"]) {
    if (key in draft) draft[key] = numOrEmpty(draft[key]);
  }
  return draft;
}

/** Краткое описание ссылки источника для строки каталога. */
export function describeSourceRef(source: string, ref: GlobalVariableSourceRef | null | undefined): string {
  if (!ref) return "";
  if (source === "template") {
    const when = str(ref.when);
    return `${str(ref.template)}${when ? ` (${WHEN_LABELS[when] ?? when})` : ""}`;
  }
  if (source === "static") return `= ${str(ref.value)}`;
  if (source === "stand_ref") return `${str(ref.stand_id)}.${str(ref.field)}`;
  const parts = [str(ref.field)];
  if (str(ref.fallback)) parts.push(`→ ${str(ref.fallback)}`);
  if (str(ref.credential_part)) parts.push(`[${str(ref.credential_part)}]`);
  if (typeof ref.segments === "number") parts.push(`segments=${ref.segments}`);
  if (typeof ref.uu_segments === "number") parts.push(`uu_segments=${ref.uu_segments}`);
  return parts.filter(Boolean).join(" ");
}

export function refValid(source: string, draft: GlobalVariableSourceRef): boolean {
  if (NO_REF_SOURCES.has(source) || source === "static" || source === "template") return true;
  if (source === "stand_ref") return str(draft.stand_id) !== "" && str(draft.field) !== "";
  if (source === "department_integration" || FIELD_ONLY_SOURCES.has(source)
    || FIELD_FALLBACK_SOURCES.has(source) || source === "os_version") {
    return str(draft.field) !== "";
  }
  return true;
}
