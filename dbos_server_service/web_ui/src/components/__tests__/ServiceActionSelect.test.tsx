import { describe, it, expect, vi, beforeEach } from "vitest";
import { useState } from "react";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import { ServiceActionSelect } from "@/components/ui/ServiceActionSelect";
import { listServiceEvents, listServices } from "@/api/loging/services";

vi.mock("@/api/loging/services", () => ({
  listServices: vi.fn(),
  listServiceEvents: vi.fn(),
}));

const mockListServices = vi.mocked(listServices);
const mockListServiceEvents = vi.mocked(listServiceEvents);

function svcResponse(services: string[]) {
  return {
    items: services.map((s) => ({
      service: s,
      event_count: 1,
      last_event_at: "2026-06-17T00:00:00Z",
    })),
    total: services.length,
    has_more: false,
    limit: null,
    offset: null,
  };
}

function eventsResponse(service: string, actions: string[]) {
  return {
    service,
    items: actions.map((a) => ({
      action: a,
      description: null,
      default_severity: "INFO",
      registered_at: "2026-06-17T00:00:00Z",
      updated_at: "2026-06-17T00:00:00Z",
    })),
    total: actions.length,
    has_more: false,
    limit: 200,
    offset: 0,
  };
}

// Управляемая обёртка: ServiceActionSelect — контролируемый компонент, ему
// нужны внешние service/action, иначе сброс действия не виден.
function Harness({
  initialService = "",
  initialAction = "",
}: {
  initialService?: string;
  initialAction?: string;
}) {
  const [service, setService] = useState(initialService);
  const [action, setAction] = useState(initialAction);
  return (
    <ServiceActionSelect
      service={service}
      action={action}
      onServiceChange={setService}
      onActionChange={setAction}
    />
  );
}

function serviceSelect() {
  return screen
    .getByText("match_service")
    .closest("label")!
    .querySelector("select")! as HTMLSelectElement;
}

function actionSelect() {
  return screen
    .getByText("match_action")
    .closest("label")!
    .querySelector("select")! as HTMLSelectElement;
}

describe("ServiceActionSelect", () => {
  beforeEach(() => {
    mockListServices.mockReset();
    mockListServiceEvents.mockReset();
  });

  it("заполняет match_service из listServices, action заблокирован пока сервис не выбран", async () => {
    mockListServices.mockResolvedValue(svcResponse(["auth_service", "server_service"]));

    render(<Harness />);

    await waitFor(() =>
      expect(
        within(serviceSelect()).getByRole("option", { name: "auth_service" }),
      ).toBeInTheDocument(),
    );
    expect(serviceSelect()).toHaveDisplayValue("(любой сервис)");
    expect(actionSelect()).toBeDisabled();
    // Каталог действий лениво: пока сервис не выбран — не дёргается.
    expect(mockListServiceEvents).not.toHaveBeenCalled();
  });

  it("при выборе сервиса грузит его действия и разблокирует селект", async () => {
    mockListServices.mockResolvedValue(svcResponse(["auth_service"]));
    mockListServiceEvents.mockResolvedValue(
      eventsResponse("auth_service", ["user.login", "user.logout"]),
    );

    render(<Harness />);
    await waitFor(() =>
      expect(
        within(serviceSelect()).getByRole("option", { name: "auth_service" }),
      ).toBeInTheDocument(),
    );

    fireEvent.change(serviceSelect(), { target: { value: "auth_service" } });

    await waitFor(() =>
      expect(mockListServiceEvents).toHaveBeenCalledWith("auth_service", {
        limit: 200,
      }),
    );
    await waitFor(() => expect(actionSelect()).not.toBeDisabled());
    expect(
      within(actionSelect()).getByRole("option", { name: /user\.login/ }),
    ).toBeInTheDocument();
  });

  it("сбрасывает action при смене сервиса, если действия нет в новом каталоге", async () => {
    mockListServices.mockResolvedValue(svcResponse(["auth_service", "server_service"]));
    mockListServiceEvents.mockImplementation((svc: string) => {
      if (svc === "auth_service")
        return Promise.resolve(eventsResponse("auth_service", ["user.login"]));
      return Promise.resolve(eventsResponse("server_service", ["server.created"]));
    });

    render(<Harness initialService="auth_service" initialAction="user.login" />);

    await waitFor(() => expect(actionSelect()).toHaveValue("user.login"));

    fireEvent.change(serviceSelect(), { target: { value: "server_service" } });

    await waitFor(() => expect(actionSelect()).toHaveValue(""));
  });

  it("показывает «(нет зарегистрированных действий)» для пустого каталога", async () => {
    mockListServices.mockResolvedValue(svcResponse(["empty_service"]));
    mockListServiceEvents.mockResolvedValue(eventsResponse("empty_service", []));

    render(<Harness initialService="empty_service" />);

    await waitFor(() =>
      expect(
        screen.getByText("(нет зарегистрированных действий)"),
      ).toBeInTheDocument(),
    );
    // «(любое действие)» остаётся доступной опцией.
    expect(
      within(actionSelect()).getByRole("option", { name: "(любое действие)" }),
    ).toBeInTheDocument();
  });

  it("рендерит подсказки HelpTooltip у обоих полей", () => {
    mockListServices.mockResolvedValue(svcResponse([]));
    render(<Harness />);
    expect(
      screen.getByRole("button", { name: "Справка: match_service" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Справка: match_action" }),
    ).toBeInTheDocument();
  });
});
