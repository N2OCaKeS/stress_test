import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { Server as ServerIcon, MonitorPlay } from "lucide-react";
import { EntityRow } from "@/components/entity/EntityRow";
import { EntityHeader } from "@/components/entity/EntityHeader";
import {
  ReachSignal,
  PowerStateBadge,
  ReachRowBadge,
} from "@/components/entity/signals";
import { PackagesTable } from "@/components/entity/PackagesTable";
import { BookingCard } from "@/components/entity/manage/BookingCard";
import { DangerZoneCard } from "@/components/entity/manage/DangerZoneCard";
import { Badge } from "@/components/ui/Badge";

// ── Общая строка списка: сервер и ВМ рендерятся одной раскладкой ─────────────

describe("EntityRow — общая строка сервера и ВМ", () => {
  it("строка сервера: иконка, тип, IP (truncate) и неусыхаемый блок бейджей", () => {
    render(
      <EntityRow
        active={false}
        onSelect={() => {}}
        title="srv-01"
        kindLabel="сервер"
        deptLabel="Core"
        subtitle="10.10.20.11"
        icon={
          <ServerIcon data-testid="server-row-icon" aria-label="сервер" />
        }
        badges={
          <>
            <ReachRowBadge reachable latencyMs={12} />
            <Badge kind="ok">free</Badge>
          </>
        }
      />,
    );
    expect(screen.getByText("srv-01")).toBeInTheDocument();
    expect(screen.getByText("сервер")).toBeInTheDocument();
    const ip = screen.getByText("10.10.20.11");
    expect(ip).toHaveClass("truncate");
    // Блок бейджей неусыхаемый (shrink-0), чтобы не наезжать на IP.
    const badgeWrap = ip.closest(".flex.items-center.gap-2")!;
    expect(
      within(badgeWrap as HTMLElement).getByText("free").closest(".shrink-0"),
    ).not.toBeNull();
    expect(screen.getByTestId("server-row-icon")).toBeInTheDocument();
  });

  it("строка ВМ: та же раскладка, справа — состояние virsh вместо ping/бронь", () => {
    render(
      <EntityRow
        active
        onSelect={() => {}}
        title="alse-1.8-rc"
        kindLabel="ВМ"
        deptLabel="Core"
        subtitle="10.10.0.5"
        icon={<MonitorPlay />}
        badges={<PowerStateBadge state="on" />}
      />,
    );
    // Тот же контейнер обёртки, что и у сервера (div.cred-row с кнопкой внутри).
    const row = screen.getByText("alse-1.8-rc").closest(".cred-row");
    expect(row).not.toBeNull();
    expect(row!.querySelector("button")).not.toBeNull();
    expect(row!.className).toContain("active");
    // Состояние virsh рендерится общим PowerStateBadge.
    expect(screen.getByText("питание: вкл")).toBeInTheDocument();
    expect(screen.getByText("ВМ")).toBeInTheDocument();
  });

  it("режим выбора: чекбокс появляется и тогглит", () => {
    const onToggle = vi.fn();
    render(
      <EntityRow
        active={false}
        onSelect={() => {}}
        selectable
        checked={false}
        onToggleChecked={onToggle}
        title="srv-02"
        kindLabel="сервер"
        deptLabel="Core"
        subtitle="10.0.0.2"
        icon={<ServerIcon />}
        badges={<Badge>free</Badge>}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox"));
    expect(onToggle).toHaveBeenCalled();
  });
});

// ── Общая шапка карточки: сервер (IPMI) и ВМ (virsh) ─────────────────────────

describe("EntityHeader — общая шапка сервера и ВМ", () => {
  it("шапка сервера: имя, ping/ssh, питание по IPMI, без кнопки назад", () => {
    render(
      <EntityHeader
        icon={<ServerIcon />}
        name="Smoke Box"
        badges={
          <>
            <ReachSignal label="ping" reachable latencyMs={12.3} />
            <ReachSignal label="ssh" reachable latencyMs={41} />
            <PowerStateBadge state="on" />
          </>
        }
        meta={<span>meta</span>}
      >
        <div>reserve</div>
      </EntityHeader>,
    );
    expect(screen.getByRole("heading", { name: "Smoke Box" })).toBeInTheDocument();
    expect(screen.getByTitle("доступность по ping")).toBeInTheDocument();
    expect(screen.getByTitle("доступность по ssh")).toBeInTheDocument();
    // IPMI-питание = общий PowerStateBadge.
    expect(screen.getByText("питание: вкл")).toBeInTheDocument();
    expect(screen.getByText("reserve")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /К хабу/ })).not.toBeInTheDocument();
  });

  it("шапка ВМ: то же самое, но на месте IPMI — состояние virsh, есть «назад»", () => {
    const onBack = vi.fn();
    render(
      <EntityHeader
        icon={<MonitorPlay />}
        onBack={onBack}
        backLabel="К хабу"
        name="detail-vm"
        badges={
          <>
            <Badge>ВМ</Badge>
            <ReachSignal label="ping" reachable latencyMs={3} />
            <PowerStateBadge state="off" />
          </>
        }
        meta={<span>meta</span>}
      />,
    );
    expect(screen.getByRole("heading", { name: "detail-vm" })).toBeInTheDocument();
    // Состояние virsh = тот же общий PowerStateBadge (здесь выключено).
    expect(screen.getByText("питание: выкл")).toBeInTheDocument();
    const back = screen.getByRole("button", { name: /К хабу/ });
    fireEvent.click(back);
    expect(onBack).toHaveBeenCalled();
  });
});

// ── Общая таблица пакетов ────────────────────────────────────────────────────

describe("PackagesTable — общий вид пакетов сервера и ВМ", () => {
  it("рендерит колонки и фильтрует по имени", () => {
    render(
      <PackagesTable
        items={[
          { name: "bash", version: "5.1", arch: "amd64" },
          { name: "coreutils", version: "8.32" },
        ]}
        filter=""
        onFilter={() => {}}
      />,
    );
    expect(screen.getByText("Название")).toBeInTheDocument();
    expect(screen.getByText("Версия")).toBeInTheDocument();
    expect(screen.getByText("Архитектура")).toBeInTheDocument();
    expect(screen.getByText("bash")).toBeInTheDocument();
    expect(screen.getByText("coreutils")).toBeInTheDocument();
    // Версии нет → прочерк; арх нет → прочерк.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("под фильтром показывает только совпадения и счётчик", () => {
    render(
      <PackagesTable
        items={[
          { name: "bash", version: "5.1" },
          { name: "coreutils", version: "8.32" },
        ]}
        filter="bash"
        onFilter={() => {}}
      />,
    );
    expect(screen.getByText("bash")).toBeInTheDocument();
    expect(screen.queryByText("coreutils")).not.toBeInTheDocument();
    expect(screen.getByText(/показано 1 из 2/)).toBeInTheDocument();
  });
});

// ── Общие карточки управления: бронь и опасная зона ─────────────────────────

describe("BookingCard / DangerZoneCard — общие карточки управления", () => {
  it("BookingCard: свободная сущность показывает «Забронировать» и открывает форму", () => {
    const onReserve = vi.fn();
    render(
      <BookingCard
        entityWord="ВМ"
        reserved={false}
        stateLabel="free"
        canManage
        busy={false}
        onReserve={onReserve}
        onRelease={() => {}}
      />,
    );
    expect(screen.getByRole("heading", { name: /^Бронь$/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Забронировать/ }));
    // Появилась форма примечания.
    expect(
      screen.getByPlaceholderText(/ручной debug-цикл/),
    ).toBeInTheDocument();
  });

  it("BookingCard: занятая сущность показывает «Снять бронь»", () => {
    render(
      <BookingCard
        entityWord="сервер"
        reserved
        stateLabel="busy"
        note="debug"
        canManage
        busy={false}
        onReserve={() => {}}
        onRelease={() => {}}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Снять бронь/ }),
    ).toBeInTheDocument();
  });

  it("DangerZoneCard: заголовок и кнопка удаления", () => {
    const onDelete = vi.fn();
    render(
      <DangerZoneCard
        description="удаление необратимо"
        buttonLabel="Удалить ВМ"
        onDelete={onDelete}
      />,
    );
    expect(screen.getByText(/Опасная зона/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Удалить ВМ/ }));
    expect(onDelete).toHaveBeenCalled();
  });
});
