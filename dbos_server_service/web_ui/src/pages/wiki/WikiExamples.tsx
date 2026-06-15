import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { Shell } from "@/components/shell/Shell";
import {
  WikiSettingsProvider,
  useWikiSettings,
  type AuthMode,
} from "./snippet";
import { EndpointExample } from "./EndpointExample";
import { FlowExample } from "./FlowExample";
import {
  AUTH_SECTIONS,
  AUTH_FLOWS,
  SERVER_SECTIONS,
  SERVER_FLOWS,
  SECRET_SECTIONS,
  SECRET_FLOWS,
  LOGING_SECTIONS,
  LOGING_FLOWS,
} from "./examples";
import type { ApiSection, ApiFlow } from "./examples";
import type { CodeLang } from "./examples/types";

type ServiceKey = "auth" | "server" | "secret" | "loging";

const SERVICE_TABS: { key: ServiceKey; label: string }[] = [
  { key: "auth", label: "auth_service" },
  { key: "server", label: "server_service" },
  { key: "secret", label: "secret_service" },
  { key: "loging", label: "loging_service" },
];

const SERVICE_DATA: Record<
  ServiceKey,
  { sections: ApiSection[]; flows: ApiFlow[] }
> = {
  auth: { sections: AUTH_SECTIONS, flows: AUTH_FLOWS },
  server: { sections: SERVER_SECTIONS, flows: SERVER_FLOWS },
  secret: { sections: SECRET_SECTIONS, flows: SECRET_FLOWS },
  loging: { sections: LOGING_SECTIONS, flows: LOGING_FLOWS },
};

const LANG_TABS: { key: CodeLang; label: string }[] = [
  { key: "python", label: "python" },
  { key: "curl", label: "curl" },
];

function FieldInput({
  id,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  hint,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-dim" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type={type}
        className="input mono text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
      />
      {hint && <div className="text-[11px] text-dim">{hint}</div>}
    </div>
  );
}

function SettingsPanel() {
  const s = useWikiSettings();
  const [open, setOpen] = useState(false);

  return (
    <div className="card flex flex-col gap-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-2 text-sm font-semibold text-left"
      >
        <ChevronRight
          className={`w-4 h-4 transition-transform ${open ? "rotate-90" : ""}`}
        />
        Настройки примеров
        <span className="text-[11px] text-dim font-normal">
          (только в памяти сессии, не сохраняются)
        </span>
      </button>

      {open && (
        <div className="flex flex-col gap-4">
          <FieldInput
            id="wiki-base-url"
            label="Base URL"
            value={s.baseUrl}
            onChange={s.setBaseUrl}
            placeholder="https://emm.devos.astralinux.ru"
            hint="По умолчанию — текущий origin. Можно указать прямой порт сервиса, например http://localhost:8000."
          />

          <div className="flex flex-col gap-2">
            <span className="text-xs text-dim">Аутентификация</span>
            <div className="flex gap-1">
              {(["login", "pat"] as AuthMode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => s.setAuthMode(m)}
                  className={`btn text-xs ${
                    s.authMode === m ? "btn-primary" : "btn-ghost"
                  }`}
                >
                  {m === "login" ? "login (username/password)" : "PAT"}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {s.authMode === "login" && (
              <>
                <FieldInput
                  id="wiki-username"
                  label="Username"
                  value={s.username}
                  onChange={s.setUsername}
                  placeholder="admin"
                />
                <FieldInput
                  id="wiki-password"
                  label="Password"
                  type="password"
                  value={s.password}
                  onChange={s.setPassword}
                  placeholder="1234"
                />
              </>
            )}
            {s.authMode === "pat" && (
              <FieldInput
                id="wiki-pat"
                label="PAT"
                type="password"
                value={s.pat}
                onChange={s.setPat}
                placeholder="dbos_pat_…"
              />
            )}
            <FieldInput
              id="wiki-token"
              label="Token (override)"
              type="password"
              value={s.token}
              onChange={s.setToken}
              placeholder="готовый access-токен — login-шаг будет пропущен"
              hint="Опционально. Если задан — используется как готовый Bearer-токен."
            />
          </div>

          <div className="flex flex-col gap-2">
            <span className="text-xs text-dim">
              Идентификаторы сущностей (подставляются в пути и тела)
            </span>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              <FieldInput
                id="wiki-department-id"
                label="department_id"
                value={s.departmentId}
                onChange={s.setDepartmentId}
                placeholder="dep_…"
              />
              <FieldInput
                id="wiki-server-id"
                label="server_id"
                value={s.serverId}
                onChange={s.setServerId}
                placeholder="srv_…"
              />
              <FieldInput
                id="wiki-account-id"
                label="account_id"
                value={s.accountId}
                onChange={s.setAccountId}
                placeholder="acc_…"
              />
              <FieldInput
                id="wiki-credential-id"
                label="credential_id"
                value={s.credentialId}
                onChange={s.setCredentialId}
                placeholder="cred_…"
              />
              <FieldInput
                id="wiki-group-id"
                label="group_id"
                value={s.groupId}
                onChange={s.setGroupId}
                placeholder="grp_…"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function GlobalLangToggle() {
  const s = useWikiSettings();
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-dim">Язык по умолчанию:</span>
      <div className="flex gap-1">
        {LANG_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => s.setLang(t.key)}
            className={`btn text-xs ${
              s.lang === t.key ? "btn-primary" : "btn-ghost"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function CollapsibleSection({
  section,
  open,
  onToggle,
}: {
  section: ApiSection;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <section
      id={section.id}
      className="flex flex-col gap-3 scroll-mt-16"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex items-start gap-2 text-left"
      >
        <ChevronRight
          className={`w-4 h-4 mt-1 shrink-0 transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <div>
          <h2 className="text-lg font-semibold">{section.title}</h2>
          {section.description && (
            <p className="text-sm text-dim">{section.description}</p>
          )}
        </div>
      </button>

      {open &&
        (section.examples.length === 0 ? (
          <div className="text-sm text-dim italic pl-6">
            Примеры скоро появятся.
          </div>
        ) : (
          section.examples.map((ex) => (
            <EndpointExample key={ex.id} example={ex} />
          ))
        ))}
    </section>
  );
}

function ServiceContent({
  service,
  sections,
  flows,
}: {
  service: ServiceKey;
  sections: ApiSection[];
  flows: ApiFlow[];
}) {
  const flowsAnchor = `flows-${service}`;
  // Аккордеон: одновременно раскрыта одна секция. Навигация — вкладки-чипы,
  // клик раскрывает секцию и подскроливает к ней.
  const [activeId, setActiveId] = useState<string | null>(null);
  useEffect(() => {
    setActiveId(null);
  }, [service]);

  const tabs = [
    ...sections.map((s) => ({ id: s.id, label: s.title })),
    ...(flows.length > 0 ? [{ id: flowsAnchor, label: "Сценарии" }] : []),
  ];

  const openAndScroll = (id: string) => {
    setActiveId(id);
    requestAnimationFrame(() => {
      document
        .getElementById(id)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };
  const toggle = (id: string) =>
    setActiveId((cur) => (cur === id ? null : id));

  return (
    <>
      <nav className="flex flex-wrap gap-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={
              "btn text-xs " +
              (activeId === t.id ? "btn-primary" : "btn-ghost")
            }
            onClick={() => openAndScroll(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {sections.map((section) => (
        <CollapsibleSection
          key={section.id}
          section={section}
          open={activeId === section.id}
          onToggle={() => toggle(section.id)}
        />
      ))}

      {flows.length > 0 && (
        <CollapsibleFlows
          anchor={flowsAnchor}
          flows={flows}
          open={activeId === flowsAnchor}
          onToggle={() => toggle(flowsAnchor)}
        />
      )}
    </>
  );
}

function CollapsibleFlows({
  anchor,
  flows,
  open,
  onToggle,
}: {
  anchor: string;
  flows: ApiFlow[];
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <section id={anchor} className="flex flex-col gap-3 scroll-mt-16">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex items-center gap-2 text-left"
      >
        <ChevronRight
          className={`w-4 h-4 shrink-0 transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <h2 className="text-lg font-semibold">Сценарии</h2>
      </button>
      {open &&
        flows.map((flow) => <FlowExample key={flow.id} flow={flow} />)}
    </section>
  );
}

function WikiBody() {
  const [activeService, setActiveService] = useState<ServiceKey>("auth");

  return (
    <main className="flex-1 overflow-y-auto min-w-0">
      <div className="px-8 py-6 max-w-7xl w-full mx-auto flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-bold mb-1">Wiki — примеры API</h1>
          <p className="text-sm text-dim">
            Готовые curl и python-сниппеты по endpoint'ам платформы. Заполните
            настройки — каждый пример станет самодостаточным: с auth-преамбулой,
            подставленными идентификаторами и (для асинхронных задач) циклом
            опроса.
          </p>
        </div>

        <SettingsPanel />

        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex gap-2">
            {SERVICE_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                className={
                  "btn text-sm " +
                  (activeService === tab.key ? "btn-primary" : "btn-ghost")
                }
                onClick={() => setActiveService(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <GlobalLangToggle />
        </div>

        <ServiceContent
          service={activeService}
          sections={SERVICE_DATA[activeService].sections}
          flows={SERVICE_DATA[activeService].flows}
        />
      </div>
    </main>
  );
}

export function WikiExamples() {
  return (
    <Shell breadcrumb="Главная / Wiki — примеры API">
      <WikiSettingsProvider>
        <WikiBody />
      </WikiSettingsProvider>
    </Shell>
  );
}
