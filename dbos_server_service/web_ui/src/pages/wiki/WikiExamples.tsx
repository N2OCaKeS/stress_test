import { useState } from "react";
import { Shell } from "@/components/shell/Shell";
import { SnippetContext } from "./snippet";
import { EndpointExample } from "./EndpointExample";
import { FlowExample } from "./FlowExample";
import {
  AUTH_SECTIONS,
  AUTH_FLOWS,
  SERVER_SECTIONS,
  SERVER_FLOWS,
} from "./examples";
import type { ApiSection, ApiFlow } from "./examples";

type ServiceKey = "auth" | "server";

const SERVICE_TABS: { key: ServiceKey; label: string }[] = [
  { key: "auth", label: "auth_service" },
  { key: "server", label: "server_service" },
];

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
  return (
    <>
      <nav className="card flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {sections.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            className="text-accent hover:underline"
          >
            {s.title}
          </a>
        ))}
        {flows.length > 0 && (
          <a href={`#${flowsAnchor}`} className="text-accent hover:underline">
            Сценарии
          </a>
        )}
      </nav>

      {sections.map((section) => (
        <section
          key={section.id}
          id={section.id}
          className="flex flex-col gap-3 scroll-mt-16"
        >
          <div>
            <h2 className="text-lg font-semibold">{section.title}</h2>
            {section.description && (
              <p className="text-sm text-dim">{section.description}</p>
            )}
          </div>
          {section.examples.length === 0 ? (
            <div className="text-sm text-dim italic">
              Примеры скоро появятся.
            </div>
          ) : (
            section.examples.map((ex) => (
              <EndpointExample key={ex.id} example={ex} />
            ))
          )}
        </section>
      ))}

      {flows.length > 0 && (
        <section
          id={flowsAnchor}
          className="flex flex-col gap-3 scroll-mt-16"
        >
          <h2 className="text-lg font-semibold">Сценарии</h2>
          {flows.map((flow) => (
            <FlowExample key={flow.id} flow={flow} />
          ))}
        </section>
      )}
    </>
  );
}

export function WikiExamples() {
  const [baseUrl, setBaseUrl] = useState(
    typeof window !== "undefined" ? window.location.origin : ""
  );
  const [token, setToken] = useState("");
  const [activeService, setActiveService] = useState<ServiceKey>("auth");

  return (
    <Shell breadcrumb="Главная / Wiki — примеры API">
      <SnippetContext.Provider value={{ baseUrl, token }}>
        <main className="flex-1 overflow-y-auto min-w-0">
          <div className="px-8 py-6 max-w-5xl w-full mx-auto flex flex-col gap-6">
            <div>
              <h1 className="text-2xl font-bold mb-1">Wiki — примеры API</h1>
              <p className="text-sm text-dim">
                Готовые curl и python-сниппеты по endpoint'ам платформы. Задайте
                Base URL и токен — они подставятся в примеры при копировании.
              </p>
            </div>

            <div className="card flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs text-dim" htmlFor="wiki-base-url">
                  Base URL
                </label>
                <input
                  id="wiki-base-url"
                  className="input mono text-sm"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://emm.devos.astralinux.ru"
                />
                <div className="text-[11px] text-dim">
                  По умолчанию — текущий origin (UI проксирует <code>/api/*</code>).
                  Можно указать прямой порт сервиса, например{" "}
                  <code>http://localhost:8000</code>.
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-dim" htmlFor="wiki-token">
                  Token
                </label>
                <input
                  id="wiki-token"
                  className="input mono text-sm"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="$TOKEN (оставьте пустым — подставится shell-переменная)"
                />
              </div>
            </div>

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

            {activeService === "auth" ? (
              <ServiceContent
                service="auth"
                sections={AUTH_SECTIONS}
                flows={AUTH_FLOWS}
              />
            ) : (
              <ServiceContent
                service="server"
                sections={SERVER_SECTIONS}
                flows={SERVER_FLOWS}
              />
            )}
          </div>
        </main>
      </SnippetContext.Provider>
    </Shell>
  );
}
