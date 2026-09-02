import { describe, expect, it } from "vitest";
import {
  EMPTY_DT,
  formatMsk,
  formatMskDate,
  formatMskShort,
  mskDateOffset,
} from "@/lib/datetime";

describe("formatMsk", () => {
  it("конвертит UTC в MSK (+3) с суффиксом", () => {
    expect(formatMsk("2026-06-11T09:00:00Z")).toBe("2026-06-11 12:00:00 MSK");
  });

  it("переносит дату через полночь UTC", () => {
    // 22:30 UTC → 01:30 MSK следующего дня
    expect(formatMsk("2026-06-11T22:30:00Z")).toBe("2026-06-12 01:30:00 MSK");
  });

  it("принимает Date", () => {
    expect(formatMsk(new Date("2026-01-01T00:00:00Z"))).toBe(
      "2026-01-01 03:00:00 MSK",
    );
  });

  it("отдаёт прочерк на пустых значениях", () => {
    expect(formatMsk(null)).toBe(EMPTY_DT);
    expect(formatMsk(undefined)).toBe(EMPTY_DT);
    expect(formatMsk("")).toBe(EMPTY_DT);
  });

  it("возвращает исходную строку на невалидном вводе", () => {
    expect(formatMsk("not-a-date")).toBe("not-a-date");
  });
});

describe("formatMskShort", () => {
  it("без секунд и суффикса", () => {
    expect(formatMskShort("2026-06-11T09:05:30Z")).toBe("2026-06-11 12:05");
  });

  it("прочерк на null", () => {
    expect(formatMskShort(null)).toBe(EMPTY_DT);
  });
});

describe("formatMskDate", () => {
  it("берёт день по московскому календарю", () => {
    // 21:30 UTC уже 00:30 следующего дня в MSK
    expect(formatMskDate("2026-06-11T21:30:00Z")).toBe("2026-06-12");
  });

  it("день не сдвигается в середине суток", () => {
    expect(formatMskDate("2026-06-11T10:00:00Z")).toBe("2026-06-11");
  });

  it("прочерк на null", () => {
    expect(formatMskDate(null)).toBe(EMPTY_DT);
  });
});

describe("mskDateOffset", () => {
  it("формат YYYY-MM-DD", () => {
    expect(mskDateOffset(0)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("смещение на N дней монотонно", () => {
    const d0 = mskDateOffset(0);
    const d1 = mskDateOffset(1);
    const d180 = mskDateOffset(180);
    expect(d1 > d0).toBe(true);
    expect(d180 > d1).toBe(true);
  });
});
