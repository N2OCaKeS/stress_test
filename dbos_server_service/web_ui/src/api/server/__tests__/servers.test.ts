import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/api/client", () => ({
  apiPost: vi.fn(() => Promise.resolve({ task_id: "task-1", status: "queued" })),
}));

import { apiPost } from "@/api/client";
import { installNodeExporter } from "@/api/server/servers";

describe("servers api client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("installNodeExporter POST'ит /servers/{id}/install-node-exporter", async () => {
    const res = await installNodeExporter("srv-103");
    expect(apiPost).toHaveBeenCalledWith(
      "/server/v1/servers/srv-103/install-node-exporter",
    );
    expect(res).toEqual({ task_id: "task-1", status: "queued" });
  });
});
