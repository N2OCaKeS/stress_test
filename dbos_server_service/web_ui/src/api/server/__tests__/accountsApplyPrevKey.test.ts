import { describe, it, expect, vi, beforeEach } from "vitest";

const apiGetMock = vi.fn();
const apiPostMock = vi.fn();

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPost: (...a: unknown[]) => apiPostMock(...a),
  };
});

import { ApiError } from "@/api/client";
import {
  applyAccountCredentials,
  revealPreviousAccountSshPrivateKey,
} from "@/api/server/accounts";

describe("applyAccountCredentials", () => {
  beforeEach(() => {
    apiPostMock.mockReset();
    apiPostMock.mockResolvedValue({
      batch_id: "b1",
      mode: "all",
      status: "queued",
      dispatched: [],
      failed: [],
      tasks: [],
      skipped: [],
      partial_failure: false,
      next_action: null,
    });
  });

  it("бьёт POST на /server-accounts/{id}/apply без тела", async () => {
    await applyAccountCredentials("acc1");
    expect(apiPostMock).toHaveBeenCalledWith(
      "/server/v1/server-accounts/acc1/apply",
    );
  });

  it("возвращает сводку диспатча как есть", async () => {
    const res = await applyAccountCredentials("acc1");
    expect(res.status).toBe("queued");
    expect(res.partial_failure).toBe(false);
  });
});

describe("revealPreviousAccountSshPrivateKey", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
  });

  it("бьёт GET на /server-accounts/{id}/previous_ssh_private_key", async () => {
    apiGetMock.mockResolvedValue({
      id: "acc1",
      login: "dbos-svc",
      ssh_private_key: "-----BEGIN OPENSSH PRIVATE KEY-----\n…",
      ssh_public_key: null,
    });
    const res = await revealPreviousAccountSshPrivateKey("acc1");
    expect(apiGetMock).toHaveBeenCalledWith(
      "/server/v1/server-accounts/acc1/previous_ssh_private_key",
    );
    expect(res.ssh_private_key).toContain("PRIVATE KEY");
  });

  it("пробрасывает 404 ACCOUNT_NO_PREVIOUS_SSH_KEY", async () => {
    apiGetMock.mockRejectedValue(
      new ApiError(404, {
        error: "not_found",
        error_code: "ACCOUNT_NO_PREVIOUS_SSH_KEY",
        message: "no previous key",
      }),
    );
    await expect(
      revealPreviousAccountSshPrivateKey("acc1"),
    ).rejects.toMatchObject({
      status: 404,
      errorCode: "ACCOUNT_NO_PREVIOUS_SSH_KEY",
    });
  });
});
