import { describe, it, expect, vi } from "vitest";
import { resolveBuildSha, buildVersionPayload } from "../write-version.mjs";

describe("resolveBuildSha", () => {
  it("берёт SHA из GIT_SHA, если задан, не дёргая git", () => {
    const exec = vi.fn();
    expect(resolveBuildSha({ GIT_SHA: "abc1234" }, exec)).toBe("abc1234");
    expect(exec).not.toHaveBeenCalled();
  });

  it("падает на CI_COMMIT_SHA / SOURCE_VERSION по порядку", () => {
    const exec = vi.fn();
    expect(resolveBuildSha({ CI_COMMIT_SHA: "ci00001" }, exec)).toBe("ci00001");
    expect(resolveBuildSha({ SOURCE_VERSION: "src0001" }, exec)).toBe("src0001");
  });

  it("обрезает длинный SHA из окружения до 12 символов", () => {
    const exec = vi.fn();
    expect(resolveBuildSha({ GIT_SHA: "abcdef1234567890" }, exec)).toBe("abcdef123456");
  });

  it("вызывает git rev-parse, если окружение пустое", () => {
    const exec = vi.fn().mockReturnValue(Buffer.from("deadbee\n"));
    expect(resolveBuildSha({}, exec)).toBe("deadbee");
    expect(exec).toHaveBeenCalledWith("git rev-parse --short HEAD", expect.any(Object));
  });

  it("возвращает unknown, если git недоступен (Docker build без .git)", () => {
    const exec = vi.fn().mockImplementation(() => {
      throw new Error("not a git repository");
    });
    expect(resolveBuildSha({}, exec)).toBe("unknown");
  });
});

describe("buildVersionPayload", () => {
  it("собирает sha и builtAt в ISO-формате", () => {
    const exec = vi.fn();
    const payload = buildVersionPayload({ GIT_SHA: "abc1234" }, exec);
    expect(payload).toEqual({
      sha: "abc1234",
      builtAt: expect.any(String),
    });
    expect(() => new Date(payload.builtAt).toISOString()).not.toThrow();
  });
});
