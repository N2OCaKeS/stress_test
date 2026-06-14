import { useState } from "react";
import { CodeBlock } from "./CodeBlock";
import { applySnippet, useSnippet } from "./snippet";
import type { CodeLang } from "./examples/types";

interface CodeExampleProps {
  curl: string;
  python: string;
}

const TABS: { key: CodeLang; label: string }[] = [
  { key: "curl", label: "curl" },
  { key: "python", label: "python" },
];

export function CodeExample({ curl, python }: CodeExampleProps) {
  const [lang, setLang] = useState<CodeLang>("curl");
  const ctx = useSnippet();
  const raw = lang === "curl" ? curl : python;
  const code = applySnippet(raw, ctx);

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
      <CodeBlock code={code} />
    </div>
  );
}
