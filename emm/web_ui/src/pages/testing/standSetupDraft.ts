/** Черновик блока «Настройка стенда» карточки теста. */

import type { StandSetup } from "@/api/testing/types";

export interface StandSetupDraft {
  params: string;
  script: string;
  runAs: StandSetup["run_as"];
  phase: StandSetup["phase"];
  rebootAfter: "auto" | "yes" | "no";
  timeout: string;
}

const CMDLINE_RE = /^[A-Za-z0-9._,:=/+-]{1,128}$/;

export function standSetupDraft(setup: StandSetup | null | undefined): StandSetupDraft {
  return {
    params: (setup?.kernel_cmdline_extra ?? []).join(" "),
    script: setup?.script ?? "",
    runAs: setup?.run_as ?? "root",
    phase: setup?.phase ?? "after_boot",
    rebootAfter: setup?.reboot_after == null ? "auto" : setup.reboot_after ? "yes" : "no",
    timeout: String(setup?.timeout_seconds ?? 1800),
  };
}

/** Черновик → `stand_setup` для API; строка — ошибка валидации; `null` — шага нет. */
export function draftToStandSetup(d: StandSetupDraft): StandSetup | null | string {
  const params = d.params.split(/\s+/).filter(Boolean);
  const bad = params.find((p) => !CMDLINE_RE.test(p));
  if (bad) return `Параметр ядра «${bad}»: только буквы, цифры и ._,:=/+-`;
  if (params.length === 0 && d.script.trim() === "") return null;
  const timeout = Number(d.timeout);
  if (!Number.isInteger(timeout) || timeout < 10 || timeout > 86400) {
    return "Таймаут скрипта настройки — целое число секунд от 10 до 86400.";
  }
  return {
    kernel_cmdline_extra: params,
    script: d.script,
    run_as: d.runAs,
    phase: d.phase,
    reboot_after: d.rebootAfter === "auto" ? null : d.rebootAfter === "yes",
    timeout_seconds: timeout,
  };
}
