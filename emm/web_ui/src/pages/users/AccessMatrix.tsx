import { lazy, Suspense } from "react";
import type { AccessGraphSubject } from "./AccessGraph";

/**
 * Граф доступа субъекта (пользователь / группа). React Flow грузится lazy,
 * чтобы тяжёлая зависимость не попадала в основной бандл — узел рисуется
 * только когда открыта вкладка «Матрица доступа».
 */

const AccessGraph = lazy(() => import("./AccessGraph"));

function GraphFallback() {
  return (
    <div className="flex-1 flex items-center justify-center p-10">
      <div className="text-sm text-dim">Загрузка визуализации…</div>
    </div>
  );
}

export function AccessMatrix({ subject }: { subject: AccessGraphSubject }) {
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-y-auto">
      <Suspense fallback={<GraphFallback />}>
        <AccessGraph subject={subject} />
      </Suspense>
    </div>
  );
}

/** Backward-compatible wrapper — узкая подпись для user-вкладки. */
export function UserAccessMatrices({ userId }: { userId: string }) {
  return <AccessMatrix subject={{ kind: "user", id: userId }} />;
}
