import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: { id: "u1", dept_id: "dep_1", platform_role: "dep_admin", service_roles: {} },
  }),
}));

const listProfiles = vi.fn();
const listVersions = vi.fn();
const createVersion = vi.fn();
const createProfile = vi.fn();
const updateProfile = vi.fn();
vi.mock("@/api/testing/launchProfiles", () => ({
  listLaunchProfiles: (...a: unknown[]) => listProfiles(...a),
  listLaunchProfileVersions: (...a: unknown[]) => listVersions(...a),
  createLaunchProfileVersion: (...a: unknown[]) => createVersion(...a),
  createLaunchProfile: (...a: unknown[]) => createProfile(...a),
  updateLaunchProfile: (...a: unknown[]) => updateProfile(...a),
}));

import { ServicesTestingLaunchProfile } from "@/pages/admin/services/ServicesTestingLaunchProfile";
import { diffLines } from "@/lib/diffLines";

function version(v: number, script: string) {
  return {
    id: `lpv_${v}`, profile_id: "lp_default", version: v, comment: v === 1 ? "сид" : "правка",
    starter_script: script,
    clone: { repo_url: "https://git/repo.git", mode: "branch", depth: null, credential: "git" },
    paths: {
      script: "{TEST_HOME}/starter.sh", dates: "{TEST_HOME}/dates_{QUEUE_ITEM_ID}.conf",
      token: "{TEST_HOME}/git_token_{QUEUE_ITEM_ID}.conf", testenv_marker: "{TEST_HOME}/testenv_marker.conf",
      command_file: "{TEST_HOME}/command.txt",
    },
    launch_command_template: "sudo bash {STARTER_PATH} {TEST_BRANCH}",
    stop_command_template: "sudo kill-tree {{STARTER_PGREP_PATTERN}}",
    stop_grace_seconds: 10, use_pty: true,
    testenv: { on_value: "on", off_value: "off", cleanup_other: false },
    created_by: null, created_at: "2026-09-24T10:00:00Z",
  };
}

function profile(over: Record<string, unknown> = {}) {
  return {
    id: "lp_default", department_id: null, name: "Легаси starter.sh", is_default: true,
    current_version: version(2, "#!/bin/bash\necho new\n"),
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", ...over,
  };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesTestingLaunchProfile />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesTestingLaunchProfile — профиль запуска", () => {
  beforeEach(() => {
    listProfiles.mockReset().mockResolvedValue({ items: [profile()] });
    listVersions.mockReset().mockResolvedValue([version(2, "#!/bin/bash\necho new\n"), version(1, "#!/bin/bash\necho old\n")]);
    createVersion.mockReset().mockResolvedValue(version(3, "x"));
    createProfile.mockReset().mockResolvedValue(profile({ id: "lp_dep", department_id: "dep_1" }));
    updateProfile.mockReset();
  });

  it("правка скрипта сохраняется новой версией", async () => {
    renderPage();
    const script = (await screen.findByLabelText("Текст starter.sh")) as HTMLTextAreaElement;
    expect(script.value).toBe("#!/bin/bash\necho new\n");
    expect(screen.getByRole("button", { name: "Сохранить как новую версию" })).toBeDisabled();

    fireEvent.change(script, { target: { value: "#!/bin/bash\necho v3\n" } });
    fireEvent.change(screen.getByLabelText("Комментарий к версии"), { target: { value: "v3" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить как новую версию" }));

    await waitFor(() => expect(createVersion).toHaveBeenCalledTimes(1));
    const [id, body] = createVersion.mock.calls[0];
    expect(id).toBe("lp_default");
    expect(body.starter_script).toBe("#!/bin/bash\necho v3\n");
    expect(body.comment).toBe("v3");
    expect(body.use_pty).toBe(true);
  });

  it("дополнительный файл уходит в новую версию", async () => {
    renderPage();
    await screen.findByLabelText("Текст starter.sh");
    fireEvent.click(screen.getByRole("button", { name: "Добавить файл" }));
    fireEvent.change(screen.getByLabelText("Путь файла 1"), { target: { value: "{TEST_HOME}/tokens.json" } });
    fireEvent.change(screen.getByLabelText("Права файла 1"), { target: { value: "0600" } });
    fireEvent.change(screen.getByLabelText("Содержимое файла 1"), {
      target: { value: '{"srv_pass": "{{TEST_PASSWORD}}"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить как новую версию" }));
    await waitFor(() => expect(createVersion).toHaveBeenCalledTimes(1));
    expect(createVersion.mock.calls[0][1].extra_files).toEqual([
      { path: "{TEST_HOME}/tokens.json", content: '{"srv_pass": "{{TEST_PASSWORD}}"}', mode: "0600", sensitive: false },
    ]);
  });

  it("общий профиль: создать профиль отдела на его основе", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Создать профиль отдела на его основе" }));
    await waitFor(() => expect(createProfile).toHaveBeenCalledTimes(1));
    const body = createProfile.mock.calls[0][0];
    expect(body.department_id).toBe("dep_1");
    expect(body.is_default).toBe(true);
    expect(body.version.starter_script).toBe("#!/bin/bash\necho new\n");
  });

  it("история версий и сравнение со старой", async () => {
    renderPage();
    expect(await screen.findAllByTestId("launch-profile-version")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Сравнить с текущей" }));
    const diff = await screen.findByLabelText("Сравнение версий");
    expect(diff.textContent).toContain("- echo old");
    expect(diff.textContent).toContain("+ echo new");
  });

  it("diffLines — построчное сравнение", () => {
    expect(diffLines("a\nb\nc", "a\nx\nc")).toEqual([
      { kind: "same", text: "a" }, { kind: "del", text: "b" }, { kind: "add", text: "x" }, { kind: "same", text: "c" },
    ]);
  });
});
