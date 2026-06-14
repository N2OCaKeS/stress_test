import { useState } from "react";
import { CodeBlock } from "./CodeBlock";
import {
  applySnippet,
  composeSnippet,
  useWikiSettings,
} from "./snippet";
import type { ApiExample, CodeLang } from "./examples/types";

interface CodeExampleProps {
  // Полный пример endpoint'а — рендерится через composeSnippet (auth-преамбула,
  // подстановка идентификаторов, poll-цикл для async).
  example?: ApiExample;
  // Сырые сниппеты — используются flow-шагами, которые сами несут свою преамбулу
  // (первый шаг flow), поэтому им нужна только подстановка {{BASE_URL}}/{{TOKEN}}.
  curl?: string;
  python?: string;
  // flow-шаг: без auth-преамбулы, только applySnippet.
  flowStep?: boolean;
}

const TABS: { key: CodeLang; label: string }[] = [
  { key: "python", label: "python" },
  { key: "curl", label: "curl" },
];

function blockLang(lang: CodeLang): "bash" | "python" {
  return lang === "curl" ? "bash" : "python";
}

export function CodeExample({
  example,
  curl,
  python,
  flowStep = false,
}: CodeExampleProps) {
  const settings = useWikiSettings();
  // Локальный язык инициализируется глобальным, дальше переключается независимо.
  const [lang, setLang] = useState<CodeLang>(settings.lang);

  const raw = lang === "curl" ? curl ?? "" : python ?? "";

  let code: string;
  if (flowStep) {
    code = applySnippet(raw, {
      baseUrl: settings.baseUrl,
      token: settings.token.trim() || settings.pat.trim(),
    });
  } else {
    const ex: ApiExample =
      example ??
      ({
        id: "",
        title: "",
        method: "",
        path: "",
        auth: "",
        description: "",
        curl: curl ?? "",
        python: python ?? "",
      } as ApiExample);
    code = composeSnippet(ex, lang, settings);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setLang(t.key)}
            className={`btn ${lang === t.key ? "btn-primary" : "btn-ghost"} text-xs`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <CodeBlock code={code} lang={blockLang(lang)} />
    </div>
  );
}
