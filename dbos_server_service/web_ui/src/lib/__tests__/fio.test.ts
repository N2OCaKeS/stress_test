import { describe, it, expect } from "vitest";
import { formatFio, formatFioShort, fullFio, hasFio } from "@/lib/fio";

describe("formatFio", () => {
  it("собирает полное ФИО из всех трёх полей", () => {
    expect(
      formatFio({
        last_name: "Иванов",
        first_name: "Иван",
        middle_name: "Иванович",
        username: "ivanov",
      }),
    ).toBe("Иванов Иван Иванович");
  });

  it("опускает пустые части ФИО", () => {
    expect(
      formatFio({ last_name: "Петров", first_name: "", middle_name: null }),
    ).toBe("Петров");
    expect(
      formatFio({ last_name: "Петров", first_name: "Пётр", middle_name: null }),
    ).toBe("Петров Пётр");
  });

  it("тримит пробелы вокруг частей", () => {
    expect(
      formatFio({ last_name: "  Сидоров ", first_name: " Семён ", middle_name: null }),
    ).toBe("Сидоров Семён");
  });

  it("фолбэк на username, когда ФИО пустое", () => {
    expect(
      formatFio({ last_name: null, first_name: "", middle_name: null, username: "jdoe" }),
    ).toBe("jdoe");
  });

  it("фолбэк на login, если username нет", () => {
    expect(formatFio({ login: "svc-bot" })).toBe("svc-bot");
  });

  it("фолбэк на «—», если нет ни ФИО, ни login", () => {
    expect(formatFio(null)).toBe("—");
    expect(formatFio({})).toBe("—");
  });

  it("уважает кастомные fallback/empty", () => {
    expect(formatFio({}, { empty: "— не задано" })).toBe("— не задано");
    expect(
      formatFio({ username: "" }, { fallback: "аноним" }),
    ).toBe("аноним");
  });
});

describe("fullFio / hasFio", () => {
  it("fullFio возвращает пустую строку без ФИО", () => {
    expect(fullFio({})).toBe("");
    expect(fullFio(null)).toBe("");
  });

  it("hasFio отражает наличие хотя бы одной части", () => {
    expect(hasFio({ first_name: "Иван" })).toBe(true);
    expect(hasFio({ last_name: "  " })).toBe(false);
    expect(hasFio(null)).toBe(false);
  });
});

describe("formatFioShort", () => {
  it("Фамилия И.О. из полного ФИО", () => {
    expect(
      formatFioShort({
        last_name: "Иванов",
        first_name: "Иван",
        middle_name: "Иванович",
      }),
    ).toBe("Иванов И.И.");
  });

  it("только фамилия и имя → Фамилия И.", () => {
    expect(
      formatFioShort({ last_name: "Иванов", first_name: "Иван" }),
    ).toBe("Иванов И.");
  });

  it("без фамилии, но с именем → Имя О.", () => {
    expect(
      formatFioShort({ first_name: "Иван", middle_name: "Иванович" }),
    ).toBe("Иван И.");
    expect(formatFioShort({ first_name: "Иван" })).toBe("Иван");
  });

  it("фолбэк на login при пустом ФИО", () => {
    expect(formatFioShort({ username: "ivanov" })).toBe("ivanov");
    expect(formatFioShort({})).toBe("—");
  });
});
