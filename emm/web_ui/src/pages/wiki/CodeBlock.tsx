import { Copy, Check } from "lucide-react";
import { useEffect, useState } from "react";
import { useToast } from "@/contexts/ToastContext";
import { useTheme } from "@/contexts/ThemeContext";
import { Button } from "@/components/ui/Button";
import { sanitizeShikiHtml } from "./shikiHtml";

export type CodeBlockLang = "bash" | "python" | "json";

interface CodeBlockProps {
  code: string;
  lang?: CodeBlockLang;
}

// Светлая тема приложения одна — vscode-light; остальные тёмные.
function isLightTheme(theme: string): boolean {
  return theme === "vscode-light";
}

const SHIKI_LIGHT = "github-light";
const SHIKI_DARK = "github-dark";

// Singleton-хайлайтер: грузится лениво при первом рендере CodeBlock, держит
// только bash/python/json и обе нужные темы. Промис кэшируется, чтобы не
// инициализировать движок повторно.
type Highlighter = {
  codeToHtml: (
    code: string,
    options: { lang: string; theme: string },
  ) => string;
};

let highlighterPromise: Promise<Highlighter> | null = null;

// Fine-grained импорт: только bash/python/json + github light/dark + JS-движок
// регэкспов (без wasm). Так сборка не тянет все грамматики Shiki.
function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = Promise.all([
      import("shiki/core"),
      import("shiki/engine/javascript"),
      import("@shikijs/langs/bash"),
      import("@shikijs/langs/python"),
      import("@shikijs/langs/json"),
      import("@shikijs/themes/github-light"),
      import("@shikijs/themes/github-dark"),
    ]).then(([core, engine, bash, python, json, light, dark]) =>
      core.createHighlighterCore({
        langs: [bash.default, python.default, json.default],
        themes: [light.default, dark.default],
        engine: engine.createJavaScriptRegexEngine(),
      }),
    );
  }
  return highlighterPromise;
}

export function CodeBlock({ code, lang = "bash" }: CodeBlockProps) {
  const toast = useToast();
  const { theme } = useTheme();
  const [copied, setCopied] = useState(false);
  const [html, setHtml] = useState<string | null>(null);

  const shikiTheme = isLightTheme(theme) ? SHIKI_LIGHT : SHIKI_DARK;

  useEffect(() => {
    let alive = true;
    setHtml(null);
    getHighlighter()
      .then((hl) => {
        if (!alive) return;
        // Не проходит проверку — null, остаёмся на plain <pre> fallback.
        setHtml(sanitizeShikiHtml(hl.codeToHtml(code, { lang, theme: shikiTheme })));
      })
      .catch(() => {
        // движок не поднялся — остаёмся на plain <pre> fallback
        if (alive) setHtml(null);
      });
    return () => {
      alive = false;
    };
  }, [code, lang, shikiTheme]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      toast.success("Скопировано");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.warn("Не удалось скопировать — выделите вручную");
    }
  };

  return (
    <div className="relative group">
      <Button variant="ghost"
        type="button"
        onClick={copy}
        title="Скопировать"
        aria-label="Скопировать код"
        className="absolute top-2 right-2 z-10 flex items-center gap-1 text-xs"
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      </Button>
      {html ? (
        <div
          className="wiki-shiki mono text-xs border border-token rounded"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="mono text-xs overflow-x-auto border border-token rounded surface-2 p-3 pr-12 whitespace-pre">
          {code}
        </pre>
      )}
    </div>
  );
}
