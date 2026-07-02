import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RotateDispatchResult } from "@/pages/server/_rotateResult";
import type { AccountRotateDispatchResponse } from "@/api/server/accounts";

// Ответ ротации несёт применение в `tasks`/`skipped` (без `dispatched`/`failed`)
// — карточка должна брать эти алиасы и рисовать счётчики и списки.
const ROTATE_RESPONSE: AccountRotateDispatchResponse = {
  batch_id: "acc1",
  mode: "all",
  status: "queued",
  dispatched: [],
  failed: [],
  tasks: [
    { server_id: "srv1", server_name: "alpha", task_id: "tsk1", status: "queued" },
  ],
  skipped: [
    { server_id: "srv2", server_name: "beta", reason: "decommissioned" },
  ],
  partial_failure: false,
  next_action: null,
};

describe("RotateDispatchResult (ответ ротации)", () => {
  it("рендерит счётчики и списки из tasks/skipped", () => {
    render(<RotateDispatchResult result={ROTATE_RESPONSE} />);
    expect(screen.getByText(/поставлено: 1/)).toBeInTheDocument();
    expect(screen.getByText(/пропущено: 1/)).toBeInTheDocument();
    // Имя сервера из ответа.
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
    // Человекочитаемая причина пропуска.
    expect(
      screen.getByText(/сервер выведен из эксплуатации/),
    ).toBeInTheDocument();
  });

  it("для аккаунта без привязок показывает нулевую раскатку", () => {
    render(
      <RotateDispatchResult
        result={{ ...ROTATE_RESPONSE, tasks: [], skipped: [] }}
      />,
    );
    expect(screen.getByText(/поставлено: 0/)).toBeInTheDocument();
    expect(screen.queryByText(/пропущено:/)).not.toBeInTheDocument();
  });
});
