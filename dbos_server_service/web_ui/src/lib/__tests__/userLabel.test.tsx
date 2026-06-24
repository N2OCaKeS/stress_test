import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// USE_MOCK_AUTH читается на этапе импорта модулей; для реального резолва имени
// гасим мок-режим и подменяем батч-endpoint лейблов перед динамическим
// импортом. Каждый кейс — свежий модульный реестр (resetModules).
const getUserLabelsMock = vi.fn(async (_ids: string[]) => ({}) as Record<string, string>);

vi.mock("@/api/auth/users", () => ({
  getUserLabels: (ids: string[]) => getUserLabelsMock(ids),
}));

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

  it("резолвит username батч-endpoint'ом и показывает имя", async () => {
    getUserLabelsMock.mockResolvedValue({ usr_a: "alice" });
    const { useUserLabel } = await import("@/lib/labels");

    function Probe() {
      return <span>{useUserLabel("usr_a")}</span>;
    }
    render(<Probe />);

    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    expect(getUserLabelsMock).toHaveBeenCalledWith(["usr_a"]);
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

  it("баннер занятости показывает имя держателя брони, а не id", async () => {
    getUserLabelsMock.mockResolvedValue({
      usr_0f8e73b1: "ivan.petrov",
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
      expect(screen.getByText("ivan.petrov")).toBeInTheDocument(),
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

  it("резолвит набор id одним запросом и отдаёт имена", async () => {
    getUserLabelsMock.mockResolvedValue({ usr_x: "alice", usr_y: "bob" });
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

    await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();

    // Один сетевой вызов на всю выборку; null/пустые в запрос не уходят.
    expect(getUserLabelsMock).toHaveBeenCalledTimes(1);
    expect(getUserLabelsMock).toHaveBeenCalledWith(["usr_x", "usr_y"]);
  });

  it("фоллбэк на id для непришедших имён", async () => {
    getUserLabelsMock.mockResolvedValue({ usr_known: "carol" });
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

    await waitFor(() => expect(screen.getByText("carol")).toBeInTheDocument());
    expect(screen.getByText("usr_gone")).toBeInTheDocument();
  });
});
