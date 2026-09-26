/**
 * Настройки интеграции отдела (Jira / Confluence / Bitbucket)
 * (`/admin/services.testing.integration_settings`).
 *
 * Без этих настроек ни СТП+Zephyr, ни HR-отчёт по активности физически не
 * работают — базовые URL внешних систем и id учётных данных (сами
 * секреты заводятся в `secret_service`, здесь только ссылки на них).
 *
 * Источник истины — `testing_service`:
 *   GET/PUT /api/testing/v1/department-integration-settings/{department_id}
 * Гейтится тем же кругом, что и «Стенды пула» / «Пересчёт статистики»:
 * department_admin своего отдела или носитель `admin` service-роли
 * testing_service. Перенесено с главной страницы (dep_admin Home) сюда —
 * настройка, а не оперативный виджет.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Link2 } from "lucide-react";
import { Link } from "react-router-dom";

import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getDepartmentIntegrationSettings,
  upsertDepartmentIntegrationSettings,
} from "@/api/testing/departmentIntegrationSettings";
import type { DepartmentIntegrationSettingsUpdateRequest } from "@/api/testing/types";
import { Button } from "@/components/ui/Button";
import { ServiceCredentialFields } from "@/pages/home/ServiceCredentialFields";

/**
 * Поля `department_integration_settings` в порядке отображения. Ключи должны
 * дословно совпадать с `DepartmentIntegrationSettingsUpdateRequest` —
 * читаем/пишем их через generic `Record<string, string | null>`, не
 * перечисляя каждое поле руками.
 */
const INTEGRATION_FIELDS: Array<{ key: string; label: string; placeholder?: string; mono?: boolean }> = [
  { key: "jira_base_url", label: "Jira base URL", placeholder: "https://jira.astralinux.ru", mono: true },
  { key: "confluence_base_url", label: "Confluence base URL", placeholder: "https://confluence.astralinux.ru", mono: true },
  { key: "bitbucket_base_url", label: "Bitbucket base URL", placeholder: "https://bitbucket.astralinux.ru", mono: true },
  { key: "bitbucket_project_key", label: "Bitbucket project key", placeholder: "PROJ" },
  { key: "bitbucket_repo_slug", label: "Bitbucket repo slug", placeholder: "my-repo" },
  { key: "jira_board_id", label: "Jira board id", placeholder: "42" },
  { key: "tempo_team_id", label: "Tempo team id", placeholder: "7" },
  { key: "confluence_report_page_space", label: "Confluence space для отчёта по активностям", placeholder: "DEPT" },
  { key: "confluence_report_parent_page_title", label: "Родительская страница отчёта по активностям", placeholder: "Отчёты по активности" },
  { key: "stp_matrix_confluence_space", label: "Confluence space для СТП-матрицы", placeholder: "DEPTQA" },
  { key: "stp_matrix_confluence_root_page_title", label: "Корневая страница СТП-матрицы", placeholder: "Состав тестового прогона" },
  // Шаблоны с подстановками `{CODE}` глобальных переменных; пусто — легаси-дефолт.
  { key: "zephyr_folder_path_template", label: "Шаблон пути папки Zephyr (прогоны СТП)", placeholder: "/stress_test/{RC_RELEASE}/{RC_NAME}", mono: true },
  { key: "zephyr_run_name_template", label: "Шаблон имени прогона Zephyr", placeholder: "{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}", mono: true },
  { key: "credential_id", label: "Credential id (Jira/Zephyr)", placeholder: "cred_...", mono: true },
  { key: "confluence_credential_id", label: "Credential id (Confluence)", placeholder: "cred_...", mono: true },
  { key: "bitbucket_credential_id", label: "Credential id (Bitbucket)", placeholder: "cred_...", mono: true },
  { key: "git_credential_id", label: "Credential id (клонирование git)", placeholder: "cred_...", mono: true },
];

const _CREDENTIAL_SELECT_KEYS = [
  "credential_id",
  "confluence_credential_id",
  "bitbucket_credential_id",
  "git_credential_id",
];

export function ServicesTestingIntegrationSettings() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <Link2 className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">
            Интеграции отдела (Jira / Confluence / Bitbucket)
          </h1>
          <div className="text-xs text-dim">testing_service · настройки отдела</div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {departmentId ? (
          <IntegrationSettingsForm departmentId={departmentId} />
        ) : (
          <div className="text-sm text-dim">Нет привязанного отдела.</div>
        )}
      </div>
    </div>
  );
}

function IntegrationSettingsForm({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const settingsQ = useQuery(() => getDepartmentIntegrationSettings(departmentId), [departmentId]);
  const loaded = settingsQ.data as unknown as Record<string, string | null> | undefined;
  const [form, setForm] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!loaded) return;
    const next: Record<string, string> = {};
    for (const f of INTEGRATION_FIELDS) next[f.key] = loaded[f.key] ?? "";
    setForm(next);
  }, [loaded]);

  const dirty = useMemo(() => {
    if (!loaded) return false;
    return INTEGRATION_FIELDS.some((f) => (form[f.key] ?? "").trim() !== (loaded[f.key] ?? ""));
  }, [loaded, form]);

  async function handleSave() {
    if (pending || !loaded) return;
    setPending(true);
    try {
      const body: Record<string, string | null> = {};
      for (const f of INTEGRATION_FIELDS) {
        const v = (form[f.key] ?? "").trim();
        body[f.key] = v === "" ? null : v;
      }
      await upsertDepartmentIntegrationSettings(
        departmentId,
        body as DepartmentIntegrationSettingsUpdateRequest,
      );
      toast.success("Настройки интеграции отдела сохранены");
      settingsQ.refetch();
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить настройки интеграции"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="text-xs text-dim">
        Нужны для генерации СТП (Zephyr), публикации СТП-матрицы и отчёта по активностям отдела в Confluence. Сами токены/пароли
        заводятся в <Link to="/secret/service" className="text-accent">сервисных учётных данных</Link>.
        Выберите нужные записи вашего отдела ниже.
      </div>

      {loaded && !loaded.credential_id && !loaded.confluence_credential_id && !loaded.bitbucket_credential_id && !loaded.git_credential_id && (
        <div className="alert-warn text-sm flex items-start gap-2">
          <Link2 className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>Реальные токены Jira, Git и Confluence ещё не введены — свежий dev-стенд их не заводит автоматически.</div>
            <Link to="/home/integration-onboarding" className="text-accent">Ввести Jira / Git / Confluence →</Link>
          </div>
        </div>
      )}

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
          <div className="grid gap-3 sm:grid-cols-2">
            {INTEGRATION_FIELDS.filter((f) => !_CREDENTIAL_SELECT_KEYS.includes(f.key)).map((f) => (
              <label key={f.key} className="flex flex-col gap-1 text-sm">
                <span className="field-label">{f.label}</span>
                <input
                  className={`field-input ${f.mono ? "mono" : ""}`.trim()}
                  value={form[f.key] ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                />
              </label>
            ))}
            <ServiceCredentialFields departmentId={departmentId} values={form} disabled={pending}
              onChange={(key, value) => setForm((prev) => ({ ...prev, [key]: value }))} />
          </div>
          <div className="flex items-center gap-3">
            <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            {dirty && !pending && <span className="text-xs text-dim">есть несохранённые изменения</span>}
          </div>
        </>
      )}
    </div>
  );
}
