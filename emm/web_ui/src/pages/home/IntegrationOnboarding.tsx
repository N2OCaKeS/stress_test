/**
 * Guided onboarding для реальных интеграционных токенов отдела
 * (Jira/Zephyr/Tempo, Bitbucket, Confluence/life, клонирование git) — G3 плана
 * `obsidian/plans/2026-09-11-testing-development.md`.
 *
 * Каждый `make seed` пересоздаёт отдел с нуля (`scripts/seed_dev.py`):
 * несекретные адреса/space'ы/ключи проектов `department_integration_settings`
 * заполняются dev-дефолтами автоматически, но `credential_id` /
 * `confluence_credential_id` / `bitbucket_credential_id` /
 * `git_credential_id` не могут быть угаданы — это ссылки на настоящие
 * секреты внешних систем, которые вводит только владелец. Эта страница —
 * узкий, шаговый вход именно для этих значений: создать/обновить
 * `scope=service`-запись в secret_service и привязать её сюда. Полная форма со всеми полями настроек интеграции
 * остаётся в `HomeDepAdmin` (`DepartmentIntegrationSettingsCard`) — эта
 * страница её не заменяет и не дублирует.
 *
 * Секрет никогда не сохраняется нигде, кроме одного вызова
 * `createCredential`/`updateCredential` — ни в state дольше формы, ни в логах.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, ArrowLeft, KeyRound } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { useQuery } from "@/api/auth/useQuery";
import { apiErrMsg } from "@/api/client";
import { isDepAdmin, isPlatformWideAdmin, personaDeptId } from "@/lib/rbac";
import { formatMsk } from "@/lib/datetime";
import {
  getDepartmentIntegrationSettings,
  upsertDepartmentIntegrationSettings,
} from "@/api/testing/departmentIntegrationSettings";
import { createCredential, getCredential, updateCredential } from "@/api/secret/credentials";
import type { Credential } from "@/api/secret/types";
import type { DepartmentIntegrationSettingsUpdateRequest } from "@/api/testing/types";

type SlotKey =
  | "credential_id"
  | "confluence_credential_id"
  | "bitbucket_credential_id"
  | "git_credential_id";

interface SlotSpec {
  key: SlotKey;
  title: string;
  hint: string;
  credentialName: string;
  credentialService: string;
}

const SLOTS: SlotSpec[] = [
  {
    key: "credential_id",
    title: "Jira / Zephyr / Tempo",
    hint: "Токен доступа к Jira — используется генерацией СТП, созданием Zephyr test-run и отчётом Tempo.",
    credentialName: "jira-integration-token",
    credentialService: "jira",
  },
  {
    key: "bitbucket_credential_id",
    title: "Bitbucket (REST API)",
    hint: "Пароль сервисной учётки Bitbucket — подсчёт коммитов для HR-отчёта по активности (basic auth, без схемы).",
    credentialName: "bitbucket-integration-token",
    credentialService: "bitbucket",
  },
  {
    key: "git_credential_id",
    title: "Клонирование git на стенде",
    hint: "Значение заголовка Authorization целиком, со схемой: «Bearer <токен>». Стенд клонирует им ветку с исходниками теста.",
    credentialName: "git-clone-header",
    credentialService: "bitbucket",
  },
  {
    key: "confluence_credential_id",
    title: "Confluence / life",
    hint: "Токен доступа к Confluence — публикация комментария по прогону, СТП-матрицы и HR-отчёта по активности.",
    credentialName: "confluence-integration-token",
    credentialService: "confluence",
  },
];

export function IntegrationOnboarding() {
  const { persona } = usePersona();
  const deptId = personaDeptId(persona);
  const allowed = (isDepAdmin(persona) || isPlatformWideAdmin(persona)) && !!deptId;

  const settingsQ = useQuery(
    () => getDepartmentIntegrationSettings(deptId as string),
    [deptId],
    { enabled: allowed },
  );

  if (!allowed) {
    return (
      <Shell breadcrumb="Настройка интеграций">
        <section className="flex-1 min-w-0 flex items-center justify-center">
          <div className="empty-card max-w-md text-center">
            <AlertCircle className="w-10 h-10 mx-auto text-warn mb-3" />
            <div className="text-sm font-medium mb-2">Раздел недоступен</div>
            <div className="text-xs text-dim">
              Ввод реальных интеграционных токенов доступен администратору отдела.
            </div>
          </div>
        </section>
      </Shell>
    );
  }

  return (
    <Shell breadcrumb="Настройка интеграций / Jira, Git, Confluence">
      <section className="flex-1 min-w-0 overflow-y-auto">
        <div className="scroll-block p-6 max-w-3xl mx-auto flex flex-col gap-5">
          <Link to="/home" className="text-xs text-accent flex items-center gap-1 w-fit">
            <ArrowLeft className="w-3.5 h-3.5" /> На главную
          </Link>

          <div>
            <h1 className="text-xl font-semibold flex items-center gap-2">
              <KeyRound className="w-5 h-5 text-accent" /> Реальные учётные данные интеграций
            </h1>
            <p className="text-sm text-dim mt-1">
              После наполнения dev-стенда отдел заново создаётся без ссылок на настоящие Jira/Git/Confluence — их нельзя
              подставить автоматически. Здесь нужно ввести только перечисленные ниже значения; остальные несекретные параметры
              (адреса, ключи проектов, Confluence space) уже заполнены dev-дефолтами и правятся при необходимости в{" "}
              <Link to="/home" className="text-accent">Интеграциях отдела</Link>.
            </p>
          </div>

          {settingsQ.loading && !settingsQ.data && <div className="text-xs text-dim">Загрузка…</div>}
          {settingsQ.error && (
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

          {settingsQ.data && (
            <div className="grid gap-4">
              {SLOTS.map((slot) => (
                <CredentialSlotCard
                  key={slot.key}
                  slot={slot}
                  departmentId={deptId as string}
                  credentialId={settingsQ.data ? settingsQ.data[slot.key] : null}
                  onLinked={() => settingsQ.refetch()}
                />
              ))}
            </div>
          )}
        </div>
      </section>
    </Shell>
  );
}

function CredentialSlotCard({
  slot,
  departmentId,
  credentialId,
  onLinked,
}: {
  slot: SlotSpec;
  departmentId: string;
  credentialId: string | null;
  onLinked: () => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [rotating, setRotating] = useState(false);

  const credQ = useQuery(
    () => getCredential(credentialId as string),
    [credentialId],
    { enabled: !!credentialId },
  );
  const cred: Credential | undefined = credentialId ? credQ.data : undefined;
  const configured = !!credentialId;

  async function handleSubmit() {
    if (submitting || !value.trim()) return;
    setSubmitting(true);
    try {
      if (credentialId) {
        // Ротация: перешифровать значение существующей записи, ссылка не меняется.
        await updateCredential(credentialId, { secret: value });
        toast.success(`${slot.title}: значение обновлено`);
      } else {
        // Первый ввод: завести scope=service запись отдела и сразу привязать её.
        const created = await createCredential({
          name: slot.credentialName,
          service: slot.credentialService,
          scope: "service",
          secret: value,
          owner_dept_id: departmentId,
          visible_to_dept: false,
        });
        const body: DepartmentIntegrationSettingsUpdateRequest = { [slot.key]: created.id };
        await upsertDepartmentIntegrationSettings(departmentId, body);
        toast.success(`${slot.title}: учётные данные созданы и привязаны`);
      }
      setValue("");
      setRotating(false);
      onLinked();
    } catch (err) {
      toast.error(apiErrMsg(err, "Сохранить не удалось"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-3 mb-2">
        <h3 className="font-semibold">{slot.title}</h3>
        <Badge kind={configured ? "ok" : "neutral"}>{configured ? "настроено" : "не настроено"}</Badge>
      </div>
      <p className="text-xs text-dim mb-3">{slot.hint}</p>

      {configured && cred && (
        <div className="text-xs text-dim mb-3">
          Учётные данные <span className="mono">{cred.name}</span> · обновлены {formatMsk(cred.updated_at)}.{" "}
          <Link
            to={`/secret/service?id=${encodeURIComponent(cred.id)}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent"
          >
            Открыть в секретах
          </Link>
        </div>
      )}

      {configured && !rotating ? (
        <Button size="sm" onClick={() => setRotating(true)}>
          Заменить значение (ротация)
        </Button>
      ) : (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="password"
            className="surface-2 border border-token rounded px-2 py-1 mono flex-1 min-w-[200px]"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={configured ? "новое значение токена" : "реальный токен / пароль"}
            maxLength={8192}
            autoComplete="off"
          />
          <Button variant="primary" size="sm" disabled={submitting || !value.trim()} onClick={handleSubmit}>
            {submitting ? "Сохраняем…" : configured ? "Обновить" : "Сохранить"}
          </Button>
          {configured && (
            <Button
              size="sm"
              onClick={() => {
                setRotating(false);
                setValue("");
              }}
            >
              Отмена
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
