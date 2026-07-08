import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { UserLabel } from "@/api/auth/users";

// USE_MOCK_AUTH читается на этапе импорта модулей; для реального резолва имени
// гасим мок-режим и подменяем батч-endpoint лейблов перед динамическим
// импортом. Каждый кейс — свежий модульный реестр (resetModules).
const getUserLabelsMock = vi.fn(
  async (_ids: string[]) => ({}) as Record<string, UserLabel>,
);

vi.mock("@/api/auth/users", () => ({
  getUserLabels: (ids: string[]) => getUserLabelsMock(ids),
}));

// `/users/labels` теперь отдаёт карточку с ФИО/display_name/username вместо
// голого username — UI собирает отображаемое имя через formatFio.
function mkLabel(overrides: Partial<UserLabel> & { username: string }): UserLabel {
  return {
    user_id: `usr_${overrides.username}`,
    display_name: null,
    last_name: null,
    first_name: null,
    middle_name: null,
    ...overrides,
  };
}

describe("useUserLabel через /users/labels", () => {
  beforeEach(() => {
    vi.resetModules();
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    getUserLabelsMock.mockReset();
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
    vi.restoreAllMocks();
  });

  it("резолвит ФИО батч-endpoint'ом и показывает его вместо username", async () => {
    getUserLabelsMock.mockResolvedValue({
      usr_a: mkLabel({ username: "alice", last_name: "Иванова", first_name: "Алиса" }),
    });
    const { useUserLabel } = await import("@/lib/labels");

    function Probe() {
      return <span>{useUserLabel("usr_a")}</span>;
    }
    render(<Probe />);

    await waitFor(() =>
      expect(screen.getByText("Иванова Алиса")).toBeInTheDocument(),
    );
    expect(getUserLabelsMock).toHaveBeenCalledWith(["usr_a"]);
    // username не показывается, когда есть ФИО.
    expect(screen.queryByText("alice")).not.toBeInTheDocument();
  });

  it("без ФИО падает на display_name, затем на username", async () => {
    getUserLabelsMock.mockResolvedValue({
      usr_dn: mkLabel({ username: "carol", display_name: "Кэрол Д." }),
    });
    const { useUserLabel } = await import("@/lib/labels");

    function Probe() {
      return <span>{useUserLabel("usr_dn")}</span>;
    }
    render(<Probe />);

    await waitFor(() =>
      expect(screen.getByText("Кэрол Д.")).toBeInTheDocument(),
    );
  });

  it("фоллбэк на id, если имя не нашлось", async () => {
    getUserLabelsMock.mockResolvedValue({});
    const { useUserLabel } = await import("@/lib/labels");

    function Probe() {
      return <span>{useUserLabel("usr_missing")}</span>;
    }
    render(<Probe />);

    await waitFor(() =>
      expect(screen.getByText("usr_missing")).toBeInTheDocument(),
    );
  });

  it("баннер занятости показывает ФИО держателя брони, а не id", async () => {
    getUserLabelsMock.mockResolvedValue({
      usr_0f8e73b1: mkLabel({
        username: "ivan.petrov",
        last_name: "Петров",
        first_name: "Иван",
      }),
    });
    const { useUserLabel } = await import("@/lib/labels");

    // Повторяет разметку busy-баннера: id уходит в title, имя — в текст.
    function BusyBanner({ id }: { id: string }) {
      const label = useUserLabel(id);
      return (
        <div>
          Сервер занят: <span>busy</span> · юзер{" "}
          <span title={id}>{label}</span>
        </div>
      );
    }
    render(<BusyBanner id="usr_0f8e73b1" />);

    await waitFor(() =>
      expect(screen.getByText("Петров Иван")).toBeInTheDocument(),
    );
    expect(screen.queryByText("usr_0f8e73b1")).not.toBeInTheDocument();
  });
});

describe("useUserLabels (батч)", () => {
  beforeEach(() => {
    vi.resetModules();
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    getUserLabelsMock.mockReset();
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
    vi.restoreAllMocks();
  });

  it("резолвит набор id одним запросом и отдаёт ФИО", async () => {
    getUserLabelsMock.mockResolvedValue({
      usr_x: mkLabel({ username: "alice", last_name: "Иванова", first_name: "Алиса" }),
      usr_y: mkLabel({ username: "bob", last_name: "Петров", first_name: "Борис" }),
    });
    const { useUserLabels } = await import("@/lib/labels");

    function List() {
      const label = useUserLabels(["usr_x", "usr_y", null]);
      return (
        <ul>
          <li>{label("usr_x")}</li>
          <li>{label("usr_y")}</li>
          <li>{label(null)}</li>
        </ul>
      );
    }
    render(<List />);

    await waitFor(() =>
      expect(screen.getByText("Иванова Алиса")).toBeInTheDocument(),
    );
    expect(screen.getByText("Петров Борис")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();

    // Один сетевой вызов на всю выборку; null/пустые в запрос не уходят.
    expect(getUserLabelsMock).toHaveBeenCalledTimes(1);
    expect(getUserLabelsMock).toHaveBeenCalledWith(["usr_x", "usr_y"]);
  });

  it("фоллбэк на id для непришедших имён", async () => {
    getUserLabelsMock.mockResolvedValue({
      usr_known: mkLabel({ username: "carol", last_name: "Сидорова" }),
    });
    const { useUserLabels } = await import("@/lib/labels");

    function List() {
      const label = useUserLabels(["usr_known", "usr_gone"]);
      return (
        <ul>
          <li>{label("usr_known")}</li>
          <li>{label("usr_gone")}</li>
        </ul>
      );
    }
    render(<List />);

    await waitFor(() =>
      expect(screen.getByText("Сидорова")).toBeInTheDocument(),
    );
    expect(screen.getByText("usr_gone")).toBeInTheDocument();
  });
});
