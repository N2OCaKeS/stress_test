import { describe, expect, it } from "vitest";
import {
  isUserBanned,
  normalizeUserStatus,
  userStatusBadgeKind,
} from "@/api/auth/users";

describe("normalizeUserStatus", () => {
  it("lowercases known statuses", () => {
    expect(normalizeUserStatus("ACTIVE")).toBe("active");
    expect(normalizeUserStatus("Blocked")).toBe("blocked");
    expect(normalizeUserStatus("banned")).toBe("banned");
  });

  it("falls back to active for unknown / empty / nullish", () => {
    expect(normalizeUserStatus(undefined)).toBe("active");
    expect(normalizeUserStatus(null)).toBe("active");
    expect(normalizeUserStatus("")).toBe("active");
    expect(normalizeUserStatus("pending")).toBe("active");
  });
});

describe("isUserBanned", () => {
  it("prefers explicit is_banned flag", () => {
    expect(isUserBanned({ is_banned: true, status: "active" })).toBe(true);
    expect(isUserBanned({ is_banned: false, status: "banned" })).toBe(false);
  });

  it("derives from status when flag absent", () => {
    expect(isUserBanned({ status: "BANNED" })).toBe(true);
    expect(isUserBanned({ status: "active" })).toBe(false);
    expect(isUserBanned({})).toBe(false);
  });
});

describe("userStatusBadgeKind", () => {
  it("maps status to badge kind", () => {
    expect(userStatusBadgeKind("active")).toBe("ok");
    expect(userStatusBadgeKind("BLOCKED")).toBe("warn");
    expect(userStatusBadgeKind("banned")).toBe("danger");
    expect(userStatusBadgeKind("nonsense")).toBe("ok");
  });
});
