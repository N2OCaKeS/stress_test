import { describe, it, expect, beforeEach } from "vitest";
import {
  SEEN_MAX_ENTRIES,
  SEEN_TTL_MS,
  loadSeen,
  pruneSeen,
  saveSeen,
} from "@/api/useTerminalNotifications";
import type { SeenMap } from "@/api/useTerminalNotifications";

const NOW = 1_800_000_000_000;
const KEY = "emm.notifications.test.u1";

describe("SeenMap: очистка старых записей", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("pruneSeen выбрасывает записи старше TTL и оставляет свежие", () => {
    const seen: SeenMap = {
      old: { status: "failed", read: true, seenAt: NOW - SEEN_TTL_MS - 1 },
      fresh: { status: "failed", read: false, seenAt: NOW - SEEN_TTL_MS + 1000 },
    };
    expect(Object.keys(pruneSeen(seen, NOW))).toEqual(["fresh"]);
  });

  it("pruneSeen возвращает ту же карту, если чистить нечего", () => {
    const seen: SeenMap = { a: { status: "running", read: false, seenAt: NOW } };
    expect(pruneSeen(seen, NOW)).toBe(seen);
  });

  it("pruneSeen режет карту до лимита, оставляя самые свежие записи", () => {
    const seen: SeenMap = {};
    for (let i = 0; i < SEEN_MAX_ENTRIES + 25; i++) {
      seen[`id${i}`] = { status: "succeeded", read: true, seenAt: NOW - i };
    }
    const kept = pruneSeen(seen, NOW);
    expect(Object.keys(kept)).toHaveLength(SEEN_MAX_ENTRIES);
    expect(kept.id0).toBeDefined();
    expect(kept[`id${SEEN_MAX_ENTRIES + 24}`]).toBeUndefined();
  });

  it("saveSeen не пишет просроченное, а пустую карту удаляет из storage", () => {
    saveSeen(KEY, {
      old: { status: "failed", read: true, seenAt: Date.now() - SEEN_TTL_MS - 1000 },
    });
    expect(window.localStorage.getItem(KEY)).toBeNull();

    saveSeen(KEY, { a: { status: "failed", read: false, seenAt: Date.now() } });
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? "{}")).toHaveProperty("a");
    saveSeen(KEY, {});
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("loadSeen читает прежний формат без seenAt как свежий", () => {
    window.localStorage.setItem(KEY, JSON.stringify({ a: { status: "failed", read: true } }));
    const seen = loadSeen(KEY, NOW);
    expect(seen.a).toEqual({ status: "failed", read: true, seenAt: NOW });
  });

  it("loadSeen отбрасывает просроченные и битые записи, не падает на мусоре", () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({
        old: { status: "failed", read: true, seenAt: NOW - SEEN_TTL_MS - 1 },
        broken: { read: true },
        ok: { status: "running", read: false, seenAt: NOW },
      }),
    );
    expect(Object.keys(loadSeen(KEY, NOW))).toEqual(["ok"]);

    window.localStorage.setItem(KEY, "{not json");
    expect(loadSeen(KEY, NOW)).toEqual({});
  });
});
