import { Copy, Check } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/contexts/ToastContext";

interface CodeBlockProps {
  code: string;
}

export function CodeBlock({ code }: CodeBlockProps) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);

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
      <button
        type="button"
        onClick={copy}
        title="Скопировать"
        aria-label="Скопировать код"
        className="btn btn-ghost absolute top-2 right-2 flex items-center gap-1 text-xs"
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
      <pre className="mono text-xs overflow-x-auto border border-token rounded surface-2 p-3 pr-12 whitespace-pre">
        {code}
      </pre>
    </div>
  );
}
