import { describe, it, expect } from "vitest";
import { buildSourceRef, describeSourceRef, refValid } from "../variableSourceRef";

describe("stand_ref", () => {
  it("ссылка — стенд и поле, лишние ключи отбрасываются", () => {
    expect(buildSourceRef("stand_ref", { stand_id: "stand_1", field: "host", fallback: "x" })).toEqual({
      stand_id: "stand_1", field: "host",
    });
  });

  it("без стенда или поля ссылка неполна", () => {
    expect(refValid("stand_ref", { field: "host" })).toBe(false);
    expect(refValid("stand_ref", { stand_id: "stand_1" })).toBe(false);
    expect(refValid("stand_ref", { stand_id: "stand_1", field: "number" })).toBe(true);
  });

  it("описание в каталоге — стенд.поле", () => {
    expect(describeSourceRef("stand_ref", { stand_id: "stand_1", field: "host" })).toBe("stand_1.host");
  });
});
