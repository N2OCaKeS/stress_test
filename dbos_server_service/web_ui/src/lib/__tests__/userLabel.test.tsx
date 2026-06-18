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
});
