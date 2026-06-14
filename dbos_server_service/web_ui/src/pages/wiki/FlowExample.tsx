import { CodeExample } from "./CodeExample";
import type { ApiFlow } from "./examples/types";

export function FlowExample({ flow }: { flow: ApiFlow }) {
  return (
    <div className="card flex flex-col gap-4" id={flow.id}>
      <div className="flex flex-col gap-1">
        <h3 className="font-semibold">{flow.title}</h3>
        <p className="text-sm text-dim">{flow.description}</p>
      </div>

      <ol className="flex flex-col gap-4">
        {flow.steps.map((step, i) => (
          <li key={i} className="flex gap-3">
            <div className="w-6 h-6 rounded-full bg-accent text-white flex items-center justify-center text-xs font-semibold shrink-0">
              {i + 1}
            </div>
            <div className="flex-1 min-w-0 flex flex-col gap-2">
              <div className="font-medium text-sm">{step.title}</div>
              <p className="text-sm text-dim">{step.description}</p>
              <CodeExample curl={step.curl} python={step.python} />
            </div>
          </li>
        ))}
      </ol>

      {flow.notes && (
        <div className="text-xs text-dim border-l-2 border-token pl-3">
          {flow.notes}
        </div>
      )}
    </div>
  );
}
