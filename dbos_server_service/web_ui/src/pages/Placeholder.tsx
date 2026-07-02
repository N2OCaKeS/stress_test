import { Construction, Plug } from "lucide-react";
import { Shell } from "@/components/shell/Shell";

interface PlaceholderProps {
  title: string;
  mockupFile?: string;
}

interface NotWiredPlaceholderProps {
  breadcrumb: string;
  service: string;
  endpoints?: string[];
  note?: string;
}

/**
 * Page-level placeholder used by service screens whose backend is implemented
 * but not yet wired to the UI in live mode (server_service, secret_service,
 * server_worker, loging_service rules/retention/etc.). Keeps the Shell so
 * navigation works, prints which endpoints are missing.
 */
export function NotWiredPlaceholder({
  breadcrumb,
  service,
  endpoints,
  note,
}: NotWiredPlaceholderProps) {
  return (
    <Shell breadcrumb={breadcrumb}>
      <main className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="scroll-block max-w-3xl w-full px-8 py-12">
          <div className="card flex items-start gap-4">
            <Plug className="w-8 h-8 text-warn shrink-0 mt-1" />
            <div className="flex-1">
              <div className="text-lg font-semibold mb-1">
                {service} ещё не подключён к UI
              </div>
              <div className="text-sm text-dim">
                Бэкенд (или его часть) пока не отрисован в UI в live-режиме.
                Когда подключим — здесь появятся реальные данные.
              </div>
              {endpoints && endpoints.length > 0 && (
                <div className="mt-3 text-xs text-dim">
                  <div className="mb-1">Ожидаемые endpoint&apos;ы:</div>
                  <ul className="space-y-0.5">
                    {endpoints.map((ep) => (
                      <li key={ep} className="mono">
                        {ep}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {note && (
                <div className="text-xs text-dim mt-3 italic">{note}</div>
              )}
            </div>
          </div>
        </div>
      </main>
    </Shell>
  );
}

/**
 * Stub used for service pages whose React port is pending. Drops the page
 * into the standard Shell so navigation/theme/persona all stay live, and
 * points the reader at the matching mockup file.
 */
export function Placeholder({ title, mockupFile }: PlaceholderProps) {
  return (
    <Shell breadcrumb={title}>
      <main className="flex-1 overflow-hidden flex flex-col min-w-0">
        <div className="scroll-block max-w-3xl w-full px-8 py-12">
          <div className="card flex items-center gap-4">
            <Construction className="w-8 h-8 text-warn shrink-0" />
            <div>
              <div className="text-lg font-semibold mb-1">
                {title} — порт в работе
              </div>
              <div className="text-sm text-dim">
                Reference-mockup:{" "}
                {mockupFile ? (
                  <a
                    className="mono text-accent hover:underline"
                    href={`http://localhost:8765/${mockupFile}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    mockups/{mockupFile}
                  </a>
                ) : (
                  <span className="mono">mockups/...</span>
                )}
              </div>
              <div className="text-xs text-dim mt-2">
                Открывай рядом mockup и React-страницу — каждая страница
                портируется один-в-один по шаблону Home.tsx.
              </div>
            </div>
          </div>
        </div>
      </main>
    </Shell>
  );
}
