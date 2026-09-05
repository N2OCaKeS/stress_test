/**
 * Вкладка «Питание» карточки ВМ.
 *
 * У виртуальной машины нет BMC, поэтому серверной IPMI-вкладки для неё нет —
 * на её место встаёт этот блок. Питанием управляет libvirt на хабе: старт,
 * мягкое выключение, ребут, а также жёсткие reset/destroy. Отдельно — тумблер
 * автозапуска (`virsh autostart`). Каждая операция уходит в worker (202 +
 * task_id), исход поллится через `useTaskOutcome` и показывается баннером.
 *
 * Рендерится только для ВМ (`entity.kind === "vm"`); для сервера возвращает
 * null. Управляющие кнопки гейтятся правом `entity.canManage`. В mock-режиме
 * страницы ВМ операции на backend не уходят — диспетчер подделывается локально.
 */
import { useState } from "react";
import { Play, Power, RotateCcw, Square, Zap } from "lucide-react";
import { apiErrMsg } from "@/api/client";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/contexts/ToastContext";
import { useTaskOutcome } from "@/api/server/useTaskOutcome";
import { TaskOutcomeBanner } from "@/components/server/TaskOutcomeBanner";
import {
  setVmAutostart,
  vmPower,
  type Vm,
  type VmPowerAction,
} from "@/api/server/vms";
import type { TaskDispatchResponse } from "@/api/server/types";
import type { EntityTabProps } from "./_entity";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/Toggle";

function fakeDispatch(): TaskDispatchResponse {
  return { task_id: `task-mock-${Date.now()}`, status: "queued" };
}

export function PowerTab({
  entity,
  onEntityUpdated,
}: EntityTabProps & { onEntityUpdated?: (next: Vm) => void }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const powerOutcome = useTaskOutcome();
  const [pending, setPending] = useState(false);

  const vm = entity?.kind === "vm" ? entity.vm : null;
  const mock = entity?.kind === "vm" ? entity.mock : false;
  const canManage = entity?.kind === "vm" ? entity.canManage : false;
  const onChanged = entity?.kind === "vm" ? entity.onChanged : () => {};

  // Вкладка живёт только в карточке ВМ; у сервера есть отдельная IPMI-вкладка.
  if (!vm) return null;

  async function power(action: VmPowerAction, danger = false) {
    const ok = await confirm({
      title: `Питание: ${action}`,
      message: `Выполнить «${action}» на ВМ ${vm!.name}?`,
      confirmLabel: "Выполнить",
      danger,
    });
    if (!ok) return;
    powerOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await vmPower(vm!.id, action);
      powerOutcome.track(`power ${action}`, res.task_id, res.status);
      toast.success(`Питание «${action}» — задача поставлена`);
    } catch (e) {
      toast.error(apiErrMsg(e, "Операция питания не удалась"));
    }
  }

  async function handleToggleAutostart() {
    if (pending) return;
    const next = !vm!.autostart;
    setPending(true);
    powerOutcome.reset();
    try {
      const res = mock ? fakeDispatch() : await setVmAutostart(vm!.id, next);
      powerOutcome.track(
        `autostart ${next ? "on" : "off"} · ${vm!.name}`,
        res.task_id,
        res.status,
      );
      onEntityUpdated?.({ ...vm!, autostart: next });
      toast.success(
        `Автозапуск ВМ ${vm!.name} — ${next ? "включён" : "выключен"} (задача поставлена)`,
      );
      onChanged();
    } catch (e) {
      toast.error(apiErrMsg(e, "Не удалось изменить автозапуск"));
    } finally {
      setPending(false);
    }
  }

  const off = vm.power_state === "off";
  const on = vm.power_state === "on";

  return (
    <div className="p-5 flex flex-col gap-4 w-full">
      <div className="card">
        <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
          <Power className="w-4 h-4 text-accent" /> Питание
        </h3>

        {!canManage && (
          <div className="text-xs text-dim mb-3">
            Операции питания недоступны: нужна роль с правом управления ВМ.
          </div>
        )}

        <div className="flex gap-2 flex-wrap">
          <Button variant="primary"
            className="flex items-center gap-1"
            onClick={() => power("start")}
            disabled={!canManage || on}
          >
            <Play className="w-4 h-4" /> Start
          </Button>
          <Button
            className="flex items-center gap-1"
            onClick={() => power("shutdown")}
            disabled={!canManage || off}
          >
            <Square className="w-4 h-4" /> Shutdown
          </Button>
          <Button
            className="flex items-center gap-1"
            onClick={() => power("reboot")}
            disabled={!canManage || off}
          >
            <RotateCcw className="w-4 h-4" /> Reboot
          </Button>
          <Button
            className="flex items-center gap-1"
            onClick={() => power("reset", true)}
            disabled={!canManage || off}
            title="Hard reset (power-cycle)"
          >
            <RotateCcw className="w-4 h-4" /> Reset
          </Button>
          <Button variant="danger"
            className="flex items-center gap-1"
            onClick={() => power("destroy", true)}
            disabled={!canManage || off}
            title="Hard power-off"
          >
            <Power className="w-4 h-4" /> Destroy
          </Button>
        </div>

        <div className="flex items-center gap-3 mt-3 pt-3 border-t border-token flex-wrap">
          <Zap
            className={`w-4 h-4 ${vm.autostart ? "text-accent" : "text-dim"}`}
          />
          <div className="flex-1 text-xs text-dim">
            Автозапуск при старте хаба (<span className="mono">virsh autostart</span>):{" "}
            <b>{vm.autostart ? "включён" : "выключен"}</b>
          </div>
          <Toggle
            checked={vm.autostart}
            aria-label="Автозапуск"
            onChange={handleToggleAutostart}
            disabled={!canManage || pending}
            title="Включить/выключить автозапуск ВМ"
            label={vm.autostart ? "Автозапуск: вкл" : "Автозапуск: выкл"}
          />
        </div>

        {powerOutcome.tracked && (
          <TaskOutcomeBanner
            outcome={powerOutcome.tracked}
            className="mt-3"
            successText="Питание применено."
            onCancelled={powerOutcome.reset}
          />
        )}
      </div>
    </div>
  );
}
