import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(() => Promise.resolve({ items: [], total: 0, limit: 50, offset: 0 })),
  apiPost: vi.fn(() => Promise.resolve({ id: "box-1" })),
  apiPatch: vi.fn(() => Promise.resolve({ id: "box-1" })),
  apiDelete: vi.fn(() => Promise.resolve(undefined)),
}));

import { apiDelete, apiGet, apiPatch, apiPost } from "@/api/client";
import { toBase64 } from "@/lib/base64";
import {
  listBoxes,
  getBox,
  createBox,
  updateBox,
  deleteBox,
} from "@/api/server/boxes";

describe("boxes api client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("listBoxes GET'ит /server/v1/boxes с фильтрами", async () => {
    const res = await listBoxes({ department_id: "core", limit: 100 });
    expect(apiGet).toHaveBeenCalledWith("/server/v1/boxes", {
      query: { department_id: "core", limit: 100, offset: undefined },
    });
    expect(res).toEqual({ items: [], total: 0, limit: 50, offset: 0 });
  });

  it("listBoxes без параметров всё равно шлёт query-объект", async () => {
    await listBoxes();
    expect(apiGet).toHaveBeenCalledWith("/server/v1/boxes", {
      query: { department_id: undefined, limit: undefined, offset: undefined },
    });
  });

  it("getBox GET'ит /server/v1/boxes/{id}", async () => {
    await getBox("box-9");
    expect(apiGet).toHaveBeenCalledWith("/server/v1/boxes/box-9");
  });

  it("createBox кодирует пароль в base_user_password_b64", async () => {
    await createBox({
      name: "alse-1.8-base",
      format: "qcow2",
      download_url: "https://dl/box.qcow2",
      base_user_login: "u",
      password: "s3cret-pass",
      os_versions: ["astra-1.8"],
      initial_snapshots: ["clean"],
      department_id: "core",
    });
    expect(apiPost).toHaveBeenCalledWith("/server/v1/boxes", {
      name: "alse-1.8-base",
      format: "qcow2",
      download_url: "https://dl/box.qcow2",
      base_user_login: "u",
      base_user_password_b64: toBase64("s3cret-pass"),
      os_versions: ["astra-1.8"],
      initial_snapshots: ["clean"],
      department_id: "core",
    });
  });

  it("createBox без пароля не кладёт base_user_password_b64", async () => {
    await createBox({
      name: "b",
      format: "tar",
      download_url: "ftp://x/b.tar",
      base_user_login: "u",
      department_id: "core",
    });
    expect(apiPost).toHaveBeenCalledWith("/server/v1/boxes", {
      name: "b",
      format: "tar",
      download_url: "ftp://x/b.tar",
      base_user_login: "u",
      department_id: "core",
    });
  });

  it("updateBox PATCH'ит /boxes/{id} и кодирует новый пароль", async () => {
    await updateBox("box-1", { format: "raw", password: "new-pass" });
    expect(apiPatch).toHaveBeenCalledWith("/server/v1/boxes/box-1", {
      format: "raw",
      base_user_password_b64: toBase64("new-pass"),
    });
  });

  it("deleteBox DELETE'ит /boxes/{id}", async () => {
    await deleteBox("box-1");
    expect(apiDelete).toHaveBeenCalledWith("/server/v1/boxes/box-1");
  });
});
