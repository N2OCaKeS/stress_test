import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getUserLabels } from "@/api/auth/users";
import { setAccessToken } from "@/api/tokenStore";

function jsonResponse(status: number, body: unknown): Response {
  const h = new Headers({ "Content-Type": "application/json" });
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: h,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("getUserLabels", () => {
  beforeEach(() => {
    setAccessToken("access_test");
  });

  afterEach(() => {
    setAccessToken(null);
    vi.restoreAllMocks();
  });

  it("шлёт ids как csv-query и возвращает только labels", async () => {
    const seen: string[] = [];
    const labels = {
      usr_a: {
        user_id: "usr_a",
        username: "alice",
        display_name: null,
        last_name: "Иванова",
        first_name: "Алиса",
        middle_name: null,
      },
      usr_b: {
        user_id: "usr_b",
        username: "bob",
        display_name: "Боб",
        last_name: null,
        first_name: null,
        middle_name: null,
      },
    };
    const fetchMock = vi.fn(async (url: string) => {
      seen.push(url);
      return jsonResponse(200, { labels });
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await getUserLabels(["usr_a", "usr_b"]);

    expect(res).toEqual(labels);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toContain("/auth/v1/users/labels");
    // csv-кодирование запятой не должно ломать резолв
    expect(decodeURIComponent(seen[0])).toContain("ids=usr_a,usr_b");
  });

  it("не дёргает сеть на пустом списке", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { labels: {} }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getUserLabels([]);

    expect(res).toEqual({});
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
