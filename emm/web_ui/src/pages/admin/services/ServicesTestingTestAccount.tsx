/**
 * Тестовая учётка отдела (`/admin/services.testing.test_account`).
 *
 * Пользователь, под которым тесты отдела исполняются на стендах: логин,
 * пароль и SSH-ключ. Хранится в secret_service; подготовка стенда ставит
 * именно этот пароль и публичный ключ, testing_worker входит по приватному
 * ключу. Приватная часть ключа в UI не показывается никогда — только
 * публичная, её можно скопировать. Пароль только вводится.
 *
 * Источник истины — `testing_service`:
 *   GET/PUT /api/testing/v1/department-test-account/{department_id}
 * Видят department_admin своего отдела и носитель `admin` service-роли
 * testing_service; backend режет остальных 403.
 */

import { useMemo, useState } from "react";
import { AlertCircle, Copy, KeyRound, RefreshCw } from "lucide-react";

import { usePersona } from "@/contexts/PersonaContext";
import { personaDeptId } from "@/lib/rbac";
import { useToast } from "@/contexts/ToastContext";
import { apiErrMsg } from "@/api/client";
import { useQuery } from "@/api/auth/useQuery";
import {
  getDepartmentTestAccount,
  upsertDepartmentTestAccount,
} from "@/api/testing/departmentTestAccount";
import type {
  DepartmentTestAccount,
  DepartmentTestAccountUpdateRequest,
} from "@/api/testing/types";
import { Button } from "@/components/ui/Button";

const LOGIN_RE = /^[A-Za-z_][A-Za-z0-9._-]{0,31}$/;
const HOME_PLACEHOLDER = "{TEST_USER}";

export function ServicesTestingTestAccount() {
  const { persona } = usePersona();
  const departmentId = personaDeptId(persona);

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="border-b border-token px-5 py-4 shrink-0 flex items-center gap-3">
        <KeyRound className="w-5 h-5 text-accent" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold leading-tight">Тестовая учётка</h1>
          <div className="text-xs text-dim">testing_service · пользователь исполнения теста на стендах отдела</div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-5">
        {departmentId ? (
          <TestAccountForm departmentId={departmentId} />
        ) : (
          <div className="text-sm text-dim">Нет привязанного отдела.</div>
        )}
      </div>
    </div>
  );
}

function TestAccountForm({ departmentId }: { departmentId: string }) {
  const toast = useToast();
  const accountQ = useQuery(() => getDepartmentTestAccount(departmentId), [departmentId]);
  const [account, setAccount] = useState<DepartmentTestAccount | null>(null);
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [homeTemplate, setHomeTemplate] = useState("");
  const [pending, setPending] = useState(false);

  // Данные с сервера и сохранённая карточка сбрасывают черновик во время
  // рендера, а не цепочкой effect'ов: иначе первый кадр после загрузки
  // показывал форму «изменённой» (пустой логин против загруженного).
  const [seenData, setSeenData] = useState(accountQ.data);
  if (seenData !== accountQ.data) {
    setSeenData(accountQ.data);
    if (accountQ.data) setAccount(accountQ.data);
  }
  const [draftFor, setDraftFor] = useState<DepartmentTestAccount | null>(null);
  if (draftFor !== account) {
    setDraftFor(account);
    if (account) {
      setLogin(account.login ?? account.login_hint);
      setPassword("");
      setHomeTemplate(account.home_template);
    }
  }

  const configured = account?.configured ?? false;
  const loginTrim = login.trim();
  const homePreview = homeTemplate.split(HOME_PLACEHOLDER).join(loginTrim || "…");

  const dirty = useMemo(() => {
    if (!account) return false;
    if (!configured) return true;
    return (
      loginTrim !== (account.login ?? "") ||
      password !== "" ||
      homeTemplate.trim() !== account.home_template
    );
  }, [account, configured, loginTrim, password, homeTemplate]);

  async function submit(body: DepartmentTestAccountUpdateRequest, success: string) {
    setPending(true);
    try {
      const saved = await upsertDepartmentTestAccount(departmentId, body);
      setAccount(saved);
      toast.success(success);
    } catch (err) {
      toast.error(apiErrMsg(err, "Не удалось сохранить тестовую учётку"));
    } finally {
      setPending(false);
    }
  }

  async function handleSave() {
    if (pending || !account) return;
    if (!LOGIN_RE.test(loginTrim)) {
      toast.error("Логин: латиница, цифры, «.», «_», «-», до 32 символов, начинается с буквы или «_».");
      return;
    }
    if (!configured && !password) {
      toast.error("Задайте пароль тестовой учётки.");
      return;
    }
    const template = homeTemplate.trim();
    if (!template.startsWith("/")) {
      toast.error("Домашний каталог — абсолютный путь, например /home/{TEST_USER}.");
      return;
    }
    const body: DepartmentTestAccountUpdateRequest = {};
    if (!configured || loginTrim !== account.login) body.login = loginTrim;
    if (password) body.password = password;
    if (template !== account.home_template) body.home_template = template;
    await submit(body, "Тестовая учётка сохранена — действует со следующей подготовки стенда");
  }

  async function handleRegenerate() {
    if (pending || !configured) return;
    await submit({ regenerate_ssh_key: true }, "Новый SSH-ключ сгенерирован — действует со следующей подготовки стенда");
  }

  function copyPublicKey() {
    if (!account?.ssh_public_key) return;
    try {
      void navigator.clipboard.writeText(account.ssh_public_key);
      toast.success("Публичный ключ скопирован");
    } catch {
      // clipboard может быть недоступен (dev/http) — ключ виден в поле
    }
  }

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      {accountQ.loading && !account && <div className="text-xs text-dim py-2">Загрузка…</div>}
      {!accountQ.loading && accountQ.error != null && !account && (
        <div className="alert-danger text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <div>{apiErrMsg(accountQ.error, "Тестовая учётка не загрузилась")}</div>
            <Button variant="ghost" className="mt-2" onClick={() => accountQ.refetch()} type="button">
              Повторить
            </Button>
          </div>
        </div>
      )}

      {account && (
        <>
          {!configured && (
            <div className="alert-danger text-sm flex items-start gap-2" role="status">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <div>
                {account.credential_missing
                  ? "Запись учётки в сервисе секретов не найдена — задайте учётку заново."
                  : "Учётка не настроена: запуски тестов отдела падают с ошибкой TEST_ACCOUNT_NOT_CONFIGURED."}
              </div>
            </div>
          )}

          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">Логин</span>
              <input
                className="field-input mono"
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                placeholder={account.login_hint}
                autoComplete="off"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">Пароль</span>
              <input
                className="field-input mono"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={account.has_password ? "задан — оставьте пустым, чтобы не менять" : "обязателен"}
                autoComplete="new-password"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="field-label">Домашний каталог (шаблон)</span>
              <input
                className="field-input mono"
                value={homeTemplate}
                onChange={(e) => setHomeTemplate(e.target.value)}
                placeholder="/home/{TEST_USER}"
              />
              <span className="text-xs text-dim">
                {HOME_PLACEHOLDER} — логин. Переменная TEST_HOME: <span className="mono">{homePreview}</span>
              </span>
            </label>
          </div>

          <div className="flex flex-col gap-2">
            <span className="field-label text-sm">SSH-ключ (Ed25519)</span>
            {configured && account.ssh_public_key ? (
              <>
                <textarea
                  className="field-input mono text-xs"
                  readOnly
                  rows={3}
                  value={account.ssh_public_key}
                  aria-label="Публичный SSH-ключ"
                />
                <div className="flex items-center gap-2">
                  <Button variant="ghost" type="button" onClick={copyPublicKey}>
                    <Copy className="w-4 h-4" /> Скопировать публичный ключ
                  </Button>
                  <Button variant="ghost" type="button" onClick={handleRegenerate} disabled={pending}>
                    <RefreshCw className="w-4 h-4" /> Сгенерировать новый ключ
                  </Button>
                </div>
                <span className="text-xs text-dim">
                  Приватная часть хранится в сервисе секретов и в интерфейсе не показывается.
                </span>
              </>
            ) : (
              <span className="text-xs text-dim">
                Ключ обязателен и будет сгенерирован при первом сохранении.
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <Button variant="primary" type="button" onClick={handleSave} disabled={pending || !dirty}>
              {pending ? "Сохраняем…" : "Сохранить"}
            </Button>
            <span className="text-xs text-dim">Изменения действуют со следующей подготовки стенда.</span>
          </div>
        </>
      )}
    </div>
  );
}
