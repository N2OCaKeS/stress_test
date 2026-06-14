import { CodeExample } from "./CodeExample";
import type { ApiExample } from "./examples/types";

const METHOD_CLASS: Record<string, string> = {
  GET: "text-accent border-accent",
  POST: "text-ok border-ok",
  PATCH: "text-warn border-warn",
  PUT: "text-warn border-warn",
  DELETE: "text-danger border-danger",
};

function methodClass(method: string): string {
  return METHOD_CLASS[method.toUpperCase()] ?? "text-dim border-token";
}

export function EndpointExample({ example }: { example: ApiExample }) {
  return (
    <div className="card flex flex-col gap-3" id={example.id}>
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={`mono text-[11px] font-semibold px-1.5 py-0.5 rounded border ${methodClass(
            example.method
          )}`}
        >
          {example.method.toUpperCase()}
        </span>
        <code className="mono text-xs break-all">{example.path}</code>
      </div>

      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h3 className="font-semibold text-sm">{example.title}</h3>
        <span className="text-[11px] text-dim">{example.auth}</span>
      </div>

      <p className="text-sm text-dim">{example.description}</p>

      <CodeExample example={example} />

      {example.notes && (
        <div className="text-xs text-dim border-l-2 border-token pl-3">
          {example.notes}
        </div>
      )}
    </div>
  );
}
