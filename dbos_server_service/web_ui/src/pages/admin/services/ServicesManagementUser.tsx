/**
 * Системная (управляющая) учётка `server_service`
 * (`/admin/services.server.management_user`).
 *
 * Управляющая учётка — OS-пользователь, под которым сервис заходит на
 * подготовленные серверы. Страница редактирует её `login` и per-режим
 * настройки (доп-группы + команды при создании учётки на боксе).
 *
 * Источник истины — `GET/PUT /api/server/v1/management-user-config`. Гейтится
 * account_admin; платформенным остальным backend режет на 403, поэтому пункт
 * каталога показывается только account_admin (см. adminCatalog).
 *
 * Смена `login` на backend'е запускает cutover — обновление управляющей учётки
 * на всех подготовленных серверах. UI здесь только предупреждает; сам фан-аут
 * делает backend.
 */

import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, ServerIcon, AlertCircle, AlertTriangle } from "lucide-react";

import { useToast } from "@/contexts/ToastContext";
import { ApiError, apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getManagementUserConfig,
  putManagementUserConfig,
  type ManagementUserConfig,
  type ManagementUserMode,
  type ManagementUserModeConfig,
} from "@/api/server/managementUserConfig";

// Порядок и русские подписи режимов. Идентификаторы совпадают с backend'ом.
const MODE_ORDER: { id: ManagementUserMode; label: string }[] = [
  { id: "astra_orel", label: "Орёл" },
  { id: "astra_smolensk", label: "Смоленск" },
  { id: "astra_voronezh", label: "Воронеж" },
  { id: "other_os", label: "Другая ОС" },
];

const EMPTY_MODE: ManagementUserModeConfig = {
  groups: [],
  extra_create_commands: [],
};

type ModesState = Record<ManagementUserMode, ManagementUserModeConfig>;

// Приводим режимы из ответа к полному набору — на случай, если backend вернул
// не все ключи, форма всё равно показывает все четыре.
function normalizeModes(cfg: ManagementUserConfig | undefined): ModesState {
  const out = {} as ModesState;
  for (const { id } of MODE_ORDER) {
    const m = cfg?.modes?.[id];
    out[id] = {
      groups: [...(m?.groups ?? [])],
      extra_create_commands: [...(m?.extra_create_commands ?? [])],
    };
  }
  return out;
}

function saveError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Недостаточно прав (нужен account_admin).";
    if (e.status === 422) return "Backend отклонил конфиг (проверьте значения).";
  }
  return apiErrMsg(e, "Не удалось сохранить конфиг");
}

export function ServicesManagementUser() {
  const toast = useToast();
  const cfgQ = useQuery<ManagementUserConfig>(
    () => getManagementUserConfig(),
    [],
  );

  const loaded = cfgQ.data;
  const initialLogin = loaded?.login ?? "";

  const [login, setLogin] = useState("");
  const [modes, setModes] = useState<ModesState>(() => normalizeModes(undefined));
  const [pending, setPending] = useState(false);

  // Перезаливаем локальное состояние формы, когда приходит/обновляется конфиг.
  useEffect(() => {
    if (!loaded) return;
    setLogin(loaded.login ?? "");
    setModes(normalizeModes(loaded));
  }, [loaded]);

  const loginChanged = login.trim() !== initialLogin.trim();

  const dirty = useMemo(() => {
    if (!loaded) return false;
    if (loginChanged) return true;
    const base = normalizeModes(loaded);
    return JSON.stringify(base) !== JSON.stringify(modes);
  }, [loaded, loginChanged, modes]);

  function setModeList(
    mode: ManagementUserMode,
    field: keyof ManagementUserModeConfig,
    values: string[],
  ) {
    setModes((prev) => ({
      ...prev,
      [mode]: { ...prev[mode], [field]: values },
    }));
  }

  async function handleSave() {
    if (pending) return;
    const value = login.trim();
    if (!value) {
      toast.error("Укажите login управляющей учётки.");
      return;
    }
    setPending(true);
    try {
      await putManagementUserConfig({ login: value, modes });
      toast.success("Конфиг управляющей учётки сохранён");
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
        <ServerIcon className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Системная учётка
          </h1>
          <div className="text-xs text-dim">
            управляющая учётка server_service · login + настройки по режимам ОС
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5 flex flex-col gap-5 max-w-3xl">
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
            <div className="flex flex-col gap-2">
              <label className="flex flex-col gap-1 text-sm max-w-md">
                <span className="field-label">login управляющей учётки</span>
                <input
                  className="field-input mono"
                  value={login}
                  onChange={(e) => setLogin(e.target.value)}
                  placeholder="dbos-mgmt"
                />
              </label>
              {loginChanged && (
                <div className="alert-warn text-xs flex items-start gap-2 max-w-md">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                  <div>
                    Смена имени запустит обновление управляющей учётки на всех
                    подготовленных серверах (cutover).
                  </div>
                </div>
              )}
            </div>

            <div className="flex flex-col gap-4">
              {MODE_ORDER.map(({ id, label }) => (
                <ModeEditor
                  key={id}
                  label={label}
                  config={modes[id] ?? EMPTY_MODE}
                  onChangeGroups={(v) => setModeList(id, "groups", v)}
                  onChangeCommands={(v) =>
                    setModeList(id, "extra_create_commands", v)
                  }
                />
              ))}
            </div>

            <div className="flex items-center gap-3">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSave}
                disabled={pending || !dirty || !login.trim()}
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

function ModeEditor({
  label,
  config,
  onChangeGroups,
  onChangeCommands,
}: {
  label: string;
  config: ManagementUserModeConfig;
  onChangeGroups: (values: string[]) => void;
  onChangeCommands: (values: string[]) => void;
}) {
  return (
    <div className="border border-token rounded p-4 flex flex-col gap-4">
      <div className="text-sm font-semibold">{label}</div>
      <StringListEditor
        title="Доп-группы"
        hint="группы, в которые добавляется учётка при создании"
        placeholder="docker"
        mono
        values={config.groups}
        onChange={onChangeGroups}
      />
      <StringListEditor
        title="Команды при создании"
        hint="например, выставление уровней целостности"
        placeholder="pdpl-user -i 63 dbos-mgmt"
        mono
        values={config.extra_create_commands}
        onChange={onChangeCommands}
      />
    </div>
  );
}

function StringListEditor({
  title,
  hint,
  placeholder,
  mono,
  values,
  onChange,
}: {
  title: string;
  hint?: string;
  placeholder?: string;
  mono?: boolean;
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function add() {
    const value = draft.trim();
    if (!value) return;
    onChange([...values, value]);
    setDraft("");
  }

  function removeAt(idx: number) {
    onChange(values.filter((_, i) => i !== idx));
  }

  const inputClass = `field-input flex-1 min-w-[140px]${mono ? " mono" : ""}`;

  return (
    <div className="flex flex-col gap-2">
      <div>
        <div className="field-label">{title}</div>
        {hint && <div className="text-[11px] text-dim">{hint}</div>}
      </div>

      {values.length > 0 ? (
        <div className="flex flex-col gap-1">
          {values.map((v, i) => (
            <div
              key={`${v}-${i}`}
              className="border border-token rounded px-3 py-1.5 flex items-center gap-2"
            >
              <span className={`flex-1 text-sm break-all${mono ? " mono" : ""}`}>
                {v}
              </span>
              <button
                type="button"
                className="btn btn-sm btn-danger flex items-center gap-1"
                onClick={() => removeAt(i)}
                title="Удалить"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-xs text-dim">пусто</div>
      )}

      <div className="flex items-center gap-2">
        <input
          className={inputClass}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button
          type="button"
          className="btn flex items-center gap-1"
          onClick={add}
          disabled={!draft.trim()}
        >
          <Plus className="w-4 h-4" />
          Добавить
        </button>
      </div>
    </div>
  );
}
