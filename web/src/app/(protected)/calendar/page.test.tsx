import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Appointment } from "@/lib/api";
import { jsonResponse, makeUser } from "@/test/helpers";
import ProtectedLayout from "../layout";
import CalendarPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

const BASICS = {
  business_timezone: "UTC",
  appointment_duration_minutes: 60,
  min_booking_notice_minutes: 60,
  max_booking_days_ahead: 60,
  self_booking_enabled: true,
  business_hours: null,
};

const USERS = [
  { id: "11111111-1111-1111-1111-111111111111", email: "owner@example.com", role: "owner" },
  { id: "22222222-2222-2222-2222-222222222222", email: "tech@example.com", role: "team_member" },
];

function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    id: "dddddddd-0000-0000-0000-000000000001",
    lead_id: "aaaaaaaa-0000-0000-0000-000000000001",
    lead_name: "Pat Customer",
    assigned_to: USERS[1].id,
    assignee_email: "tech@example.com",
    subject: "Roof survey",
    notes: "",
    start_at: "2026-08-20T14:00:00Z",
    end_at: "2026-08-20T15:00:00Z",
    timezone: "UTC",
    status: "scheduled",
    origin: "staff",
    booking_reference: "APT-ABCD1234",
    cancellation_reason: null,
    created_at: "2026-08-09T12:00:00Z",
    updated_at: "2026-08-09T12:00:00Z",
    ...overrides,
  };
}

function renderCalendar(appointments: Appointment[], role: "owner" | "team_member" = "owner") {
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const url = String(args[0]);
    if (url.includes("/auth/session")) {
      return Promise.resolve(
        jsonResponse(200, { user: makeUser({ role }), csrf_token: "csrf" }),
      );
    }
    if (url.includes("/settings/scheduling-basics")) return Promise.resolve(jsonResponse(200, BASICS));
    if (url.includes("/leads/assignable-users")) return Promise.resolve(jsonResponse(200, USERS));
    if (url.includes("/appointments")) return Promise.resolve(jsonResponse(200, appointments));
    return Promise.resolve(jsonResponse(404, { detail: "Not found." }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <ProtectedLayout>
      <CalendarPage />
    </ProtectedLayout>,
  );
  return fetchMock;
}

function appointmentQueries(fetchMock: ReturnType<typeof renderCalendar>) {
  return fetchMock.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.includes("/appointments?"));
}

test("shows the week around today with each appointment on its own day", async () => {
  // 2026-08-20 is a Thursday; the week view must therefore render seven days.
  vi.setSystemTime(new Date("2026-08-18T09:00:00Z"));
  renderCalendar([appointment()]);
  expect(await screen.findByRole("heading", { name: "Calendar" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("link", { name: "Pat Customer" })).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Pat Customer" })).toHaveAttribute(
    "href",
    "/leads/aaaaaaaa-0000-0000-0000-000000000001",
  );
  expect(screen.getByText(/Times are shown in UTC/)).toBeInTheDocument();
  expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(7);
  expect(screen.getAllByText("Nothing scheduled.").length).toBe(6);
  vi.useRealTimers();
});

test("filtering by staff asks the server for that calendar only", async () => {
  vi.setSystemTime(new Date("2026-08-18T09:00:00Z"));
  const fetchMock = renderCalendar([appointment()]);
  const select = await screen.findByLabelText("Staff");
  await waitFor(() => expect(appointmentQueries(fetchMock).length).toBeGreaterThan(0));
  fireEvent.change(select, { target: { value: USERS[1].id } });

  await waitFor(() => {
    const queries = appointmentQueries(fetchMock);
    expect(queries[queries.length - 1]).toContain(`staff_id=${USERS[1].id}`);
  });
  vi.useRealTimers();
});

test("moving to the next week asks for a later range", async () => {
  vi.setSystemTime(new Date("2026-08-18T09:00:00Z"));
  const fetchMock = renderCalendar([]);
  await waitFor(() => expect(appointmentQueries(fetchMock).length).toBeGreaterThan(0));
  const before = appointmentQueries(fetchMock).at(-1)!;
  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() => {
    const after = appointmentQueries(fetchMock).at(-1)!;
    expect(after).not.toBe(before);
    expect(decodeURIComponent(after)).toContain("2026-08-2");
  });
  vi.useRealTimers();
});

test("the agenda view lists appointments in time order", async () => {
  vi.setSystemTime(new Date("2026-08-18T09:00:00Z"));
  renderCalendar([
    appointment({ id: "d2", subject: "Later job", start_at: "2026-08-21T09:00:00Z", end_at: "2026-08-21T10:00:00Z" }),
    appointment({ id: "d1", subject: "Earlier job" }),
  ]);
  fireEvent.click(await screen.findByRole("button", { name: "Agenda" }));
  await waitFor(() => expect(screen.getByText(/Earlier job/)).toBeInTheDocument());
  const items = screen.getAllByRole("listitem").map((node) => node.textContent ?? "");
  const earlier = items.findIndex((text) => text.includes("Earlier job"));
  const later = items.findIndex((text) => text.includes("Later job"));
  expect(earlier).toBeLessThan(later);
  vi.useRealTimers();
});

test("a canceled appointment is still shown, marked as canceled", async () => {
  vi.setSystemTime(new Date("2026-08-18T09:00:00Z"));
  renderCalendar([appointment({ status: "canceled" })]);
  await waitFor(() => expect(screen.getByText(/Canceled/)).toBeInTheDocument());
  vi.useRealTimers();
});

test("a team member gets no staff filter", async () => {
  vi.setSystemTime(new Date("2026-08-18T09:00:00Z"));
  const fetchMock = renderCalendar([appointment()], "team_member");
  await screen.findByRole("heading", { name: "Calendar" });
  await waitFor(() => expect(appointmentQueries(fetchMock).length).toBeGreaterThan(0));
  expect(screen.queryByLabelText("Staff")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("assignable-users"))).toBe(false);
  vi.useRealTimers();
});
