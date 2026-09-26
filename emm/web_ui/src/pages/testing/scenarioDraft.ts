/** Черновик редактора сценария и его преобразование в тело API. */

import type {
  Scenario,
  ScenarioActionIn,
  ScenarioActionKind,
  ScenarioPreparation,
  ScenarioReadiness,
  ScenarioStandIn,
  ScenarioWrite,
} from "@/api/testing/types";

import { draftToStandSetup, standSetupDraft, type StandSetupDraft } from "./standSetupDraft";

// ── черновик ─────────────────────────────────────────────────────────────

export interface StandDraft {
  stand_id: string;
  label: string;
  preparation: ScenarioPreparation;
  provisioning_profile_id: string;
  setup: StandSetupDraft;
  kernel_override: string;
  mode_override: string;
}

export interface ActionDraft {
  key: string;
  kind: ScenarioActionKind;
  stand_id: string;
  test_id: string;
  is_verdict: boolean;
  seconds: string;
}

export interface ScenarioDraft {
  code: string;
  name: string;
  readiness: ScenarioReadiness;
  stands: StandDraft[];
  actions: ActionDraft[];
}

let actionSeq = 0;
export function actionKey(): string {
  actionSeq += 1;
  return `a${actionSeq}`;
}

export function emptyDraft(): ScenarioDraft {
  return { code: "", name: "", readiness: "development", stands: [], actions: [] };
}

export function standDraft(stand_id: string, row?: ScenarioStandIn): StandDraft {
  return {
    stand_id,
    label: row?.label ?? "",
    preparation: row?.preparation ?? "full",
    provisioning_profile_id: row?.provisioning_profile_id ?? "",
    setup: standSetupDraft(row?.stand_setup),
    kernel_override: row?.kernel_override ?? "",
    mode_override: row?.mode_override ?? "",
  };
}

export function draftFrom(s: Scenario): ScenarioDraft {
  return {
    code: s.code,
    name: s.name,
    readiness: s.readiness,
    stands: s.stands.map((row) => standDraft(row.stand_id, row)),
    actions: s.actions.map((a) => ({
      key: actionKey(),
      kind: a.kind,
      stand_id: a.stand_id ?? "",
      test_id: a.test_id ?? "",
      is_verdict: a.is_verdict,
      seconds: typeof a.params.seconds === "number" ? String(a.params.seconds) : "",
    })),
  };
}

/** Черновик → тело API; строка — ошибка, которую видно до отправки. */
export function draftToWrite(d: ScenarioDraft, departmentId: string): ScenarioWrite | string {
  if (!d.code.trim() || !d.name.trim()) return "Укажите код и название сценария.";
  const stands: ScenarioStandIn[] = [];
  for (const s of d.stands) {
    const setup = draftToStandSetup(s.setup);
    if (typeof setup === "string") return setup;
    stands.push({
      stand_id: s.stand_id,
      label: s.label.trim() || null,
      preparation: s.preparation,
      provisioning_profile_id: s.provisioning_profile_id || null,
      stand_setup: setup,
      kernel_override: s.kernel_override.trim() || null,
      mode_override: (s.mode_override || null) as ScenarioStandIn["mode_override"],
    });
  }
  if (d.actions.length === 0) return "Добавьте хотя бы одно действие.";
  const actions: ScenarioActionIn[] = [];
  for (const [index, a] of d.actions.entries()) {
    const n = index + 1;
    if (a.kind === "wait") {
      const seconds = Number(a.seconds);
      if (!Number.isInteger(seconds) || seconds < 1 || seconds > 86400) {
        return `Действие ${n}: ожидание — целое число секунд от 1 до 86400.`;
      }
      actions.push({ kind: "wait", stand_id: null, test_id: null, is_verdict: false, params: { seconds } });
      continue;
    }
    if (!a.stand_id) return `Действие ${n}: выберите стенд сценария.`;
    if (a.kind === "run_test" && !a.test_id) return `Действие ${n}: выберите тест.`;
    actions.push({
      kind: a.kind,
      stand_id: a.stand_id,
      test_id: a.kind === "run_test" ? a.test_id : null,
      is_verdict: a.kind === "run_test" && a.is_verdict,
      params: {},
    });
  }
  if (!actions.some((a) => a.is_verdict)) return "Отметьте хотя бы один тест, по которому считается вердикт сценария.";
  return { code: d.code.trim(), name: d.name.trim(), department_id: departmentId, readiness: d.readiness, stands, actions };
}

export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
  const next = items.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}
