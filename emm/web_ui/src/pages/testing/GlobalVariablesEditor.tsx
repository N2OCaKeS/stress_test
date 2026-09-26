/**
 * Редактор глобальных переменных конструктора команд (раздел «Тесты» →
 * «Переменные»). Вынесен из `tests.tsx`, когда появилась форма
 * `source_ref`: выбор источника и ссылки под каждый источник
 * — шаблон `{CODE}` с подсветкой и автодополнением, поле
 * теста, поле стенда, поле интеграций отдела (с частью credential), поле
 * версии ОС, тестовой учётки, папки Zephyr.
 *
 * Допустимые поля источников приходят с сервиса
 * (`GET /global-variables/source-options`) — те же множества он проверяет
 * при сохранении. Ошибки валидации сервиса (неизвестный код в шаблоне,
 * цикл, неверная ссылка, переменная используется) показываются у поля, к
 * которому относятся, а не всплывающим сообщением.
 */
import { useMemo, useState } from "react";
import { AlertCircle, Loader2, Pencil, Plus, Trash2, Variable } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Dropdown } from "@/components/ui/Dropdown";
import { Badge } from "@/components/ui/Badge";
import { Checkbox } from "@/components/ui/Checkbox";
import { useQuery } from "@/api/auth/useQuery";
import { ApiError, apiErrMsg } from "@/api/client";
import { useToast } from "@/contexts/ToastContext";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import {
  createGlobalVariable,
  deleteGlobalVariable,
  getGlobalVariableSourceOptions,
  updateGlobalVariable,
} from "@/api/testing/global_variables";
import type {
  GlobalVariable,
  GlobalVariableCreateRequest,
  GlobalVariableSource,
  GlobalVariableSourceOptions,
  GlobalVariableSourceRef,
  GlobalVariableValueType,
} from "@/api/testing/types";
import { TemplateInput } from "./TemplateInput";
import { listNamedTestStands, standName } from "@/api/testing/standCatalogue";
import {
  FIELD_ERROR_CODES,
  FIELD_FALLBACK_SOURCES,
  FIELD_ONLY_SOURCES,
  NO_REF_SOURCES,
  SOURCE_LABELS,
  VALUE_TYPE_OPTIONS,
  WHEN_LABELS,
  buildSourceRef,
  describeSourceRef,
  draftFromRef,
  fieldOptions,
  refValid,
  str,
  unknownTemplateCodes,
} from "./variableSourceRef";

// ── модалка каталога ──────────────────────────────────────────────────────

export function GlobalVariablesModal({
  variables,
  loading,
  error,
  onRefetch,
  onClose,
}: {
  variables: GlobalVariable[];
  loading: boolean;
  error: unknown;
  onRefetch: () => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();

  const [addOpen, setAddOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Справочник полей источников. Не загрузился — форма работает, но списки
  // полей пустые: подсказка у формы.
  const optionsQ = useQuery(() => getGlobalVariableSourceOptions(), []);

  /** Ошибка у поля формы — вернуть её форме; прочее — всплывающим сообщением. */
  function fieldError(e: unknown, fallback: string): ApiError | null {
    if (e instanceof ApiError && FIELD_ERROR_CODES.has(e.errorCode)) return e;
    toast.error(apiErrMsg(e, fallback));
    return null;
  }

  async function handleCreate(body: GlobalVariableCreateRequest): Promise<ApiError | null> {
    setBusy(true);
    try {
      await createGlobalVariable(body);
      toast.success(`Переменная «${body.code}» создана`);
      setAddOpen(false);
      onRefetch();
      return null;
    } catch (e) {
      return fieldError(e, "Не удалось создать переменную");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(variable: GlobalVariable, body: GlobalVariableCreateRequest): Promise<ApiError | null> {
    setBusy(true);
    try {
      await updateGlobalVariable(variable.id, body);
      toast.success(`Переменная «${variable.code}» обновлена`);
      setEditId(null);
      onRefetch();
      return null;
    } catch (e) {
      return fieldError(e, "Не удалось сохранить переменную");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(variable: GlobalVariable) {
    const ok = await confirm({
      title: "Удалить глобальную переменную",
      message: `Удалить «${variable.code} · ${variable.label}»? Если переменную использует слот команды теста, шаблон другой переменной или override слота, удаление будет отклонено.`,
      confirmLabel: "Удалить",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteGlobalVariable(variable.id);
      toast.success(`Переменная «${variable.code}» удалена`);
      onRefetch();
    } catch (e) {
      toast.error(inUseMessage(e) ?? apiErrMsg(e, "Не удалось удалить переменную"));
    } finally {
      setBusy(false);
    }
  }

  const codes = useMemo(() => variables.map((v) => v.code), [variables]);

  return (
    <Modal
      open
      onOpenChange={(next) => !next && onClose()}
      title="Глобальные переменные"
      subtitle="Каталог переменных конструктора команд"
      icon={<Variable className="w-5 h-5 text-accent" />}
      width="lg"
    >
      <div className="flex flex-col gap-3">
        <div className="text-xs text-dim">
          Переменные, доступные слотам команды любого теста каталога: источник значения и
          ссылка в нём (<span className="mono">source_ref</span>), тип и (опционально) резолвер
          вариантов выбора (<span className="mono">choices_source</span>). Шаблоны подставляют
          другие переменные как <span className="mono">{"{CODE}"}</span>.
        </div>

        {loading ? (
          <div className="text-xs text-dim flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> Загрузка…
          </div>
        ) : error ? (
          <div className="alert alert-danger flex items-start gap-2 text-xs">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div className="flex-1">{apiErrMsg(error, "Список переменных не загрузился")}</div>
            <Button size="sm" onClick={() => onRefetch()}>Повторить</Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {variables.length === 0 && !addOpen && (
              <div className="text-xs text-dim">Переменных пока нет — добавьте первую.</div>
            )}
            {variables.map((variable) =>
              editId === variable.id ? (
                <GlobalVariableEditorForm
                  key={variable.id}
                  initial={variable}
                  codes={codes}
                  options={optionsQ.data ?? null}
                  optionsError={optionsQ.error}
                  saving={busy}
                  onCancel={() => setEditId(null)}
                  onSave={(body) => handleUpdate(variable, body)}
                />
              ) : (
                <GlobalVariableRow
                  key={variable.id}
                  variable={variable}
                  disabled={busy}
                  onEdit={() => setEditId(variable.id)}
                  onDelete={() => handleDelete(variable)}
                />
              ),
            )}
          </div>
        )}

        {addOpen ? (
          <GlobalVariableEditorForm
            codes={codes}
            options={optionsQ.data ?? null}
            optionsError={optionsQ.error}
            saving={busy}
            onCancel={() => setAddOpen(false)}
            onSave={handleCreate}
          />
        ) : (
          <Button
            type="button"
            size="sm"
            className="inline-flex items-center gap-1.5 self-start"
            onClick={() => setAddOpen(true)}
            disabled={loading}
          >
            <Plus className="w-3.5 h-3.5" /> Добавить переменную
          </Button>
        )}
      </div>
    </Modal>
  );
}

function inUseMessage(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.errorCode !== "GLOBAL_VARIABLE_IN_USE") return null;
  const vars = (e.details?.referenced_by_variables as string[] | undefined) ?? [];
  const tests = (e.details?.referenced_by_tests as string[] | undefined) ?? [];
  const where = [
    vars.length ? `шаблоны переменных ${vars.join(", ")}` : "",
    tests.length ? `override слотов тестов ${tests.join(", ")}` : "",
  ].filter(Boolean).join("; ");
  return `Переменная используется${where ? `: ${where}` : ""}`;
}

function GlobalVariableRow({
  variable,
  disabled,
  onEdit,
  onDelete,
}: {
  variable: GlobalVariable;
  disabled: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const ref = describeSourceRef(variable.source, variable.source_ref);
  return (
    <div className="surface-2 border border-token rounded p-2 flex items-center gap-2">
      <div className="flex-1 min-w-0 flex items-center gap-1.5 flex-wrap text-sm">
        <span className="mono font-medium">{variable.code}</span>
        <span className="text-dim text-xs">{variable.label}</span>
        <Badge kind="accent">{variable.source}</Badge>
        <Badge kind="ok">{variable.value_type}</Badge>
        {ref && <span className="mono text-[11px] text-dim truncate max-w-[360px]" title={ref}>{ref}</span>}
        {variable.choices_source && (
          <span className="mono text-[11px] text-dim">choices: {variable.choices_source}</span>
        )}
        {variable.is_sensitive && <Badge kind="warn">чувствительно</Badge>}
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button size="sm" onClick={onEdit} disabled={disabled} aria-label="Изменить переменную">
          <Pencil className="w-3.5 h-3.5" />
        </Button>
        <Button size="sm" variant="danger" onClick={onDelete} disabled={disabled} aria-label="Удалить переменную">
          <Trash2 className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}

function FieldError({ children }: { children: React.ReactNode }) {
  return <div role="alert" className="text-[11px] text-danger">{children}</div>;
}

// ── форма переменной ──────────────────────────────────────────────────────

function GlobalVariableEditorForm({
  initial,
  codes,
  options,
  optionsError,
  saving,
  onCancel,
  onSave,
}: {
  initial?: GlobalVariable;
  /** Коды каталога — для подсветки и автодополнения шаблона. */
  codes: string[];
  options: GlobalVariableSourceOptions | null;
  optionsError?: unknown;
  saving?: boolean;
  onCancel: () => void;
  /** Возвращает ошибку сервиса, которую форма показывает у поля. */
  onSave: (body: GlobalVariableCreateRequest) => Promise<ApiError | null | void>;
}) {
  const [code, setCode] = useState(initial?.code ?? "");
  const [label, setLabel] = useState(initial?.label ?? "");
  const [source, setSource] = useState<string>(initial?.source ?? "launch_context");
  const [draft, setDraft] = useState<GlobalVariableSourceRef>(() => draftFromRef(initial?.source_ref));
  const [valueType, setValueType] = useState<GlobalVariableValueType>(
    (initial?.value_type as GlobalVariableValueType) ?? "string",
  );
  const [choicesSource, setChoicesSource] = useState(initial?.choices_source ?? "");
  const [isSensitive, setIsSensitive] = useState(initial?.is_sensitive ?? false);
  const [description, setDescription] = useState(initial?.description ?? "");
  const [serverError, setServerError] = useState<ApiError | null>(null);

  const otherCodes = useMemo(() => codes.filter((c) => c !== initial?.code), [codes, initial?.code]);
  const valid = code.trim() !== "" && label.trim() !== "" && refValid(source, draft);

  const sourceOptions = (options?.sources ?? Object.keys(SOURCE_LABELS)).map((value) => ({
    value,
    label: SOURCE_LABELS[value as GlobalVariableSource] ?? value,
  }));

  function setRef(key: string, value: unknown) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setServerError(null);
  }

  function changeSource(next: string) {
    setSource(next);
    // Ссылка у каждого источника своя — старые ключи сервис отверг бы.
    setDraft(next === initial?.source ? draftFromRef(initial?.source_ref) : {});
    setServerError(null);
  }

  async function submit() {
    if (!valid) return;
    setServerError(null);
    const result = await onSave({
      code: code.trim(),
      label: label.trim(),
      source: source as GlobalVariableSource,
      source_ref: buildSourceRef(source, draft, options),
      value_type: valueType,
      choices_source: choicesSource.trim() || null,
      is_sensitive: isSensitive,
      description: description.trim() || null,
    });
    if (result) setServerError(result);
  }

  const errorCode = serverError?.errorCode;
  const details = serverError?.details ?? {};
  const codeError = errorCode === "GLOBAL_VARIABLE_DUPLICATE"
    ? "Переменная с таким кодом уже есть"
    : errorCode === "GLOBAL_VARIABLE_IN_USE"
      ? inUseMessage(serverError)
      : null;
  const templateError = errorCode === "VARIABLE_TEMPLATE_UNKNOWN"
    ? `Неизвестные переменные: ${((details.unknown as string[] | undefined) ?? []).join(", ")}`
    : errorCode === "VARIABLE_TEMPLATE_CYCLE"
      ? `Цикл подстановок: ${((details.cycle as string[] | undefined) ?? []).join(" → ")}`
      : null;
  const refError = errorCode === "VARIABLE_SOURCE_REF_INVALID"
    ? `${serverError?.message ?? ""}${typeof details.hint === "string" ? ` — ${details.hint}` : ""}`
    : null;

  return (
    <div className="surface border border-token rounded p-3 flex flex-col gap-2">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <div className="flex flex-col gap-1">
          <input
            className={`surface-2 border rounded px-2 py-1 mono text-sm ${codeError ? "border-danger" : "border-token"}`}
            placeholder="код, например RC"
            value={code}
            onChange={(e) => { setCode(e.target.value); setServerError(null); }}
          />
          {codeError && <FieldError>{codeError}</FieldError>}
        </div>
        <input
          className="surface-2 border border-token rounded px-2 py-1 text-sm"
          placeholder="метка"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Dropdown mode="single" options={sourceOptions} value={source} onChange={(v) => v && changeSource(v)} />
        <Dropdown
          mode="single"
          options={VALUE_TYPE_OPTIONS}
          value={valueType}
          onChange={(v) => setValueType(v as GlobalVariableValueType)}
        />
      </div>

      <SourceRefFields
        source={source}
        draft={draft}
        setRef={setRef}
        codes={otherCodes}
        options={options}
        optionsError={optionsError}
        isSensitive={isSensitive}
        templateError={templateError}
      />
      {refError && <FieldError>{refError}</FieldError>}

      <input
        className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
        placeholder="choices_source (необязательно, например dynamic:kernels)"
        value={choicesSource}
        onChange={(e) => setChoicesSource(e.target.value)}
      />
      <input
        className="surface-2 border border-token rounded px-2 py-1 text-sm"
        placeholder="описание (необязательно)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <Checkbox checked={isSensitive} onChange={(e) => { setIsSensitive(e.target.checked); setServerError(null); }} />
        Чувствительное значение (маскируется в логах и превью как ***)
      </label>
      <div className="flex items-center gap-2 justify-end">
        <Button type="button" size="sm" onClick={onCancel}>Отмена</Button>
        <Button type="button" size="sm" variant="primary" disabled={!valid || saving} onClick={submit}>
          {saving ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </div>
  );
}

/**
 * `stand_ref`: поле конкретного стенда пула — адрес второго стенда в
 * команде многостендового сценария. Стенды — своего отдела.
 */
function StandRefFields({
  draft,
  setRef,
  fields,
}: {
  draft: GlobalVariableSourceRef;
  setRef: (key: string, value: unknown) => void;
  fields: string[];
}) {
  const standsQ = useQuery(() => listNamedTestStands(), []);
  const standId = str(draft.stand_id);
  const stands = standsQ.data ?? [];
  const options = stands.map((s) => ({ value: s.id, label: standName(s) }));
  if (standId && !stands.some((s) => s.id === standId)) options.push({ value: standId, label: standId });
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
      {standsQ.error != null && (
        <div className="text-[11px] text-warn md:col-span-2">{apiErrMsg(standsQ.error, "Список стендов не загрузился")}</div>
      )}
      <label className="flex flex-col gap-1">
        <span className="text-dim text-xs">Стенд *</span>
        <Dropdown
          mode="single"
          searchable
          placeholder="— стенд —"
          options={options}
          value={standId}
          onChange={(v) => setRef("stand_id", v)}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-dim text-xs">Поле стенда *</span>
        <Dropdown
          mode="single"
          placeholder="— поле —"
          options={fieldOptions(fields)}
          value={str(draft.field)}
          onChange={(v) => setRef("field", v)}
        />
      </label>
      <div className="text-[11px] text-dim md:col-span-2">
        Значение берётся с этого стенда, на каком бы стенде ни шёл тест. Удалить стенд, на который ссылается переменная, нельзя.
      </div>
    </div>
  );
}

/** Форма `source_ref` под выбранный источник. */
function SourceRefFields({
  source,
  draft,
  setRef,
  codes,
  options,
  optionsError,
  isSensitive,
  templateError,
}: {
  source: string;
  draft: GlobalVariableSourceRef;
  setRef: (key: string, value: unknown) => void;
  codes: string[];
  options: GlobalVariableSourceOptions | null;
  optionsError?: unknown;
  isSensitive: boolean;
  templateError: string | null;
}) {
  const optionsMissing = !options && (
    FIELD_FALLBACK_SOURCES.has(source) || FIELD_ONLY_SOURCES.has(source)
    || source === "department_integration" || source === "os_version"
  );
  const field = str(draft.field);

  if (NO_REF_SOURCES.has(source)) {
    return (
      <div className="text-[11px] text-dim">
        {source === "per_test_override"
          ? "Значение задаётся в слоте команды теста (override)."
          : "Значение — из контекста запуска с тем же кодом (RC, KERNEL, MODE и значения задания)."}
      </div>
    );
  }

  if (source === "static") {
    return (
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">Значение</span>
        <input
          className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
          placeholder="фиксированное значение; пусто — из контекста запуска"
          value={str(draft.value)}
          onChange={(e) => setRef("value", e.target.value)}
        />
      </label>
    );
  }

  if (source === "template") {
    const unknown = unknownTemplateCodes(str(draft.template), codes);
    return (
      <div className="flex flex-col gap-1 text-sm">
        <span className="text-dim text-xs">Шаблон</span>
        <TemplateInput
          ariaLabel="Шаблон"
          value={str(draft.template)}
          onChange={(v) => setRef("template", v)}
          codes={codes}
          placeholder="STRESS_report {RC_NAME} ⬝ {TEST_TOPIC}"
          invalid={!!templateError}
        />
        {templateError && <FieldError>{templateError}</FieldError>}
        {!templateError && unknown.length > 0 && (
          <div className="text-[11px] text-warn">Нет в каталоге: {unknown.join(", ")} — сохранение будет отклонено.</div>
        )}
        <span className="text-[11px] text-dim">
          <span className="mono">{"{CODE}"}</span> подставляет другую переменную; после «{"{"}» — подсказка кодов.
        </span>
        <Dropdown
          mode="single"
          options={[
            { value: "", label: "применять всегда" },
            ...(options?.template_conditions ?? ["debug", "not_debug"]).map((w) => ({ value: w, label: WHEN_LABELS[w] ?? w })),
          ]}
          value={str(draft.when)}
          onChange={(v) => setRef("when", v)}
        />
        <span className="text-[11px] text-dim">Вне условия значение шаблона — пустая строка.</span>
      </div>
    );
  }

  const fieldsFor: Record<string, string[]> = {
    test_field: options?.test_fields ?? [],
    stand: options?.stand_fields ?? [],
    os_version: options?.os_version_fields ?? [],
    test_account: options?.test_account_fields ?? [],
    zephyr_folder: options?.zephyr_folder_fields ?? [],
  };

  const optionsHint = optionsMissing && (
    <div className="text-[11px] text-warn">
      {optionsError ? apiErrMsg(optionsError, "Справочник полей не загрузился") : "Загрузка справочника полей…"}
    </div>
  );

  if (source === "stand_ref") {
    return <StandRefFields draft={draft} setRef={setRef} fields={options?.stand_ref_fields ?? ["host", "legacy_token", "number"]} />;
  }

  if (source === "department_integration") {
    const diFields = options?.department_integration_fields ?? [];
    const current = diFields.find((f) => f.field === field);
    const isCredential = current?.is_credential ?? false;
    const fallbackFields = diFields.filter((f) => f.field !== field && f.is_credential === isCredential);
    const part = str(draft.credential_part);
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
        {optionsHint}
        <label className="flex flex-col gap-1">
          <span className="text-dim text-xs">Поле интеграций отдела *</span>
          <Dropdown
            mode="single"
            searchable
            placeholder="— поле —"
            options={diFields.map((f) => ({ value: f.field, label: f.is_credential ? `${f.field} (учётные данные)` : f.field }))}
            value={field}
            onChange={(v) => { setRef("field", v); setRef("fallback", ""); }}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-dim text-xs">Если пусто — поле</span>
          <Dropdown
            mode="single"
            searchable
            placeholder="— без запасного поля —"
            options={[{ value: "", label: "— без запасного поля —" }, ...fallbackFields.map((f) => ({ value: f.field, label: f.field }))]}
            value={str(draft.fallback)}
            onChange={(v) => setRef("fallback", v)}
          />
        </label>
        {isCredential && (
          <label className="flex flex-col gap-1">
            <span className="text-dim text-xs">Часть учётных данных *</span>
            <Dropdown
              mode="single"
              placeholder="— login или secret —"
              options={(options?.credential_parts ?? ["login", "secret"]).map((p) => ({
                value: p, label: p === "secret" ? "secret — секрет (только sensitive)" : "login — логин",
              }))}
              value={part}
              onChange={(v) => setRef("credential_part", v)}
            />
          </label>
        )}
        {isCredential && part === "secret" && !isSensitive && (
          <div className="text-[11px] text-warn md:col-span-2">
            Секрет учётной записи можно сохранить только в чувствительной переменной — включите флаг ниже.
          </div>
        )}
      </div>
    );
  }

  const fields = fieldsFor[source];
  if (!fields) {
    return <div className="text-[11px] text-dim">Для этого источника форма ссылки не предусмотрена.</div>;
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
      {optionsHint}
      <label className="flex flex-col gap-1">
        <span className="text-dim text-xs">
          {source === "test_field" ? "Поле теста *" : source === "stand" ? "Поле стенда *" : "Поле *"}
        </span>
        <Dropdown mode="single" placeholder="— поле —" options={fieldOptions(fields)} value={field} onChange={(v) => setRef("field", v)} />
      </label>
      {FIELD_FALLBACK_SOURCES.has(source) && (
        <label className="flex flex-col gap-1">
          <span className="text-dim text-xs">Если пусто — поле</span>
          <Dropdown
            mode="single"
            placeholder="— без запасного поля —"
            options={fieldOptions(fields.filter((f) => f !== field), "— без запасного поля —")}
            value={str(draft.fallback)}
            onChange={(v) => setRef("fallback", v)}
          />
        </label>
      )}
      {source === "os_version" && field === "name" && (
        <>
          <label className="flex flex-col gap-1">
            <span className="text-dim text-xs">Сегментов имени</span>
            <input
              type="number"
              min={1}
              className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
              placeholder="всё имя"
              value={str(draft.segments)}
              onChange={(e) => setRef("segments", e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-dim text-xs">Сегментов у срочного обновления (UU)</span>
            <input
              type="number"
              min={1}
              className="surface-2 border border-token rounded px-2 py-1 mono text-sm"
              placeholder="как у обычной версии"
              value={str(draft.uu_segments)}
              onChange={(e) => setRef("uu_segments", e.target.value)}
            />
          </label>
        </>
      )}
      {source === "test_account" && field === "password" && !isSensitive && (
        <div className="text-[11px] text-warn md:col-span-2">
          Пароль учётки можно сохранить только в чувствительной переменной — включите флаг ниже.
        </div>
      )}
    </div>
  );
}
