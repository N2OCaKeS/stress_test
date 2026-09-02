import { describe, it, expect } from "vitest";
import { ApiError, apiErrMsg } from "@/api/client";
import {
  humanErrMsg,
  validationFields,
  HUMAN_MESSAGES,
} from "@/api/errorMessages";

function err(
  status: number,
  code: string,
  message = "raw backend message",
  details?: Record<string, unknown>,
  retryAfter?: number,
  requestId?: string,
): ApiError {
  return new ApiError(
    status,
    { error: "x", error_code: code, message, details, request_id: requestId },
    retryAfter,
  );
}

describe("humanErrMsg", () => {
  it("returns a hint for a known 409 dup-name code", () => {
    expect(humanErrMsg(err(409, "USER_ALREADY_EXISTS"))).toBe(
      HUMAN_MESSAGES.USER_ALREADY_EXISTS,
    );
  });

  it("returns null for an unknown code so caller can fall back", () => {
    expect(humanErrMsg(err(500, "SOME_UNMAPPED_CODE"))).toBeNull();
  });

  it("appends a countdown for throttle codes carrying retry_after", () => {
    const msg = humanErrMsg(err(429, "RATE_LIMIT_EXCEEDED", "m", undefined, 120));
    expect(msg).toContain("Повтор через 2 мин");
    expect(msg).toContain("120 сек");
  });

  it("lists offending fields for VALIDATION_ERROR", () => {
    const msg = humanErrMsg(
      err(422, "VALIDATION_ERROR", "m", {
        errors: [
          { loc: ["body", "username"], msg: "required", type: "missing" },
          { loc: ["body", "email"], msg: "invalid", type: "value_error" },
        ],
      }),
    );
    expect(msg).toContain("username");
    expect(msg).toContain("email");
  });

  it("gives a generic validation hint when no fields parse", () => {
    expect(humanErrMsg(err(422, "VALIDATION_ERROR"))).toBe(
      "Проверьте корректность введённых данных.",
    );
  });
});

describe("validationFields", () => {
  it("strips body/query/path wrappers and dedupes", () => {
    const e = err(422, "VALIDATION_ERROR", "m", {
      errors: [
        { loc: ["body", "name"] },
        { loc: ["query", "limit"] },
        { loc: ["body", "name"] },
      ],
    });
    expect(validationFields(e).sort()).toEqual(["limit", "name"]);
  });

  it("returns [] when details has no errors array", () => {
    expect(validationFields(err(422, "VALIDATION_ERROR"))).toEqual([]);
  });
});

describe("apiErrMsg integration", () => {
  it("uses the human hint and appends the raw code in parens", () => {
    const out = apiErrMsg(err(409, "LAST_ACCOUNT_ADMIN"));
    expect(out).toContain(HUMAN_MESSAGES.LAST_ACCOUNT_ADMIN);
    expect(out).toContain("(LAST_ACCOUNT_ADMIN)");
  });

  it("keeps CODE: message format for unmapped codes", () => {
    expect(apiErrMsg(err(404, "WIDGET_NOT_FOUND", "no widget"))).toBe(
      "WIDGET_NOT_FOUND: no widget",
    );
  });

  it("surfaces the backend message for unmapped codes instead of a generic text", () => {
    const out = apiErrMsg(
      err(500, "SOME_UNMAPPED_CODE", "database is on fire"),
      "Ошибка",
    );
    expect(out).toContain("database is on fire");
    expect(out).not.toBe("Ошибка");
  });

  it("keeps request_id in the tail for support diagnostics", () => {
    const unmapped = apiErrMsg(
      err(404, "WIDGET_NOT_FOUND", "no widget", undefined, undefined, "req_42"),
    );
    expect(unmapped).toContain("no widget");
    expect(unmapped).toContain("req_42");

    const mapped = apiErrMsg(
      err(409, "LAST_ACCOUNT_ADMIN", "m", undefined, undefined, "req_99"),
    );
    expect(mapped).toContain("LAST_ACCOUNT_ADMIN");
    expect(mapped).toContain("req_99");
  });

  it("falls back for plain errors and non-errors", () => {
    expect(apiErrMsg(new Error("plain"))).toBe("plain");
    expect(apiErrMsg("nope", "fallback")).toBe("fallback");
  });
});
