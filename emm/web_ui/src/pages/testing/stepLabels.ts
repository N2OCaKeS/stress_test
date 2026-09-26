/** Подписи шагов многоступенчатого теста для конструктора команды. */

import type { TestStep } from "@/api/testing/types";

/** Подпись шага во вкладке: номер и имя. */
export function stepTitle(step: TestStep, index: number): string {
  return `${index + 1}. ${step.name || `Шаг ${index + 1}`}`;
}

/** Коротко о шаге: способ запуска, параметры ядра, скрипт, `$5`. */
export function stepSummary(step: TestStep): string {
  const parts = [step.run_mode === "rerun" ? "повторный запуск" : "полный запуск"];
  const params = step.stand_setup?.kernel_cmdline_extra ?? [];
  if (params.length > 0) parts.push(`ядро: ${params.join(" ")}`);
  if ((step.stand_setup?.script ?? "").trim()) parts.push("скрипт настройки");
  if (step.starter_suffix) parts.push(`$5=${step.starter_suffix}`);
  return parts.join(" · ");
}
