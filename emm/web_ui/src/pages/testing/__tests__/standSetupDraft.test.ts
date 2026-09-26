import { describe, it, expect } from "vitest";
import { draftToStandSetup, standSetupDraft } from "@/pages/testing/standSetupDraft";

describe("standSetupDraft — шаг настройки стенда", () => {
  it("пустой блок — шага нет", () => {
    expect(draftToStandSetup(standSetupDraft(null))).toBeNull();
  });

  it("параметры ядра и скрипт собираются в stand_setup", () => {
    const draft = { ...standSetupDraft(null), params: " audit=0  maxcpus=8 ", script: "echo {{TEST_USER}}\n" };
    expect(draftToStandSetup(draft)).toEqual({
      kernel_cmdline_extra: ["audit=0", "maxcpus=8"], script: "echo {{TEST_USER}}\n",
      run_as: "root", phase: "after_boot", reboot_after: null, timeout_seconds: 1800,
    });
  });

  it("небезопасный параметр ядра — ошибка", () => {
    expect(draftToStandSetup({ ...standSetupDraft(null), params: "audit=0;reboot" })).toMatch(/Параметр ядра/);
  });

  it("round-trip сохранённого шага", () => {
    const setup = {
      kernel_cmdline_extra: ["audit=0"], script: "", run_as: "test_user" as const,
      phase: "before_kernel" as const, reboot_after: false, timeout_seconds: 60,
    };
    expect(draftToStandSetup(standSetupDraft(setup))).toEqual(setup);
  });
});
