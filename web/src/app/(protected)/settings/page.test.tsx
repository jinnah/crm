import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../layout";
import CommunicationSettingsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

const SETTINGS = {
  business_name: "Acme Roofing",
  form_title: "Request a quote",
  form_intro: "Tell us what you need.",
  acknowledgment_enabled: false,
  acknowledgment_template: "Hi {{lead_name}}, thanks for contacting {{business_name}}.",
  alert_enabled: false,
  alert_template: "New {{source}} lead: {{lead_name}} (ref {{lead_id}}).",
  alert_destination_phone: null,
  response_target_minutes: 5,
};

const SCHEDULING = {
  business_timezone: "America/New_York",
  appointment_duration_minutes: 60,
  min_booking_notice_minutes: 120,
  max_booking_days_ahead: 60,
  buffer_before_minutes: 0,
  buffer_after_minutes: 15,
  self_booking_enabled: true,
  appointment_confirmation_enabled: true,
  appointment_reminder_enabled: true,
  reminder_offset_minutes: 1440,
  second_reminder_offset_minutes: null,
  upcoming_window_hours: 48,
  confirmation_template: "Hi {{lead_name}}, you are booked for {{appointment_date}}.",
  reminder_template: "Reminder: {{appointment_time}} with {{business_name}}.",
  appointment_canceled_template: "Your {{appointment_date}} appointment is canceled.",
  appointment_rescheduled_template: "Moved to {{appointment_date}} at {{appointment_time}}.",
  business_hours: {
    mon: [["09:00", "17:00"]],
    tue: [["09:00", "17:00"]],
    wed: [["09:00", "17:00"]],
    thu: [["09:00", "17:00"]],
    fri: [["09:00", "17:00"]],
    sat: [],
    sun: [],
  },
};

function renderSettings(
  role: "owner" | "manager" = "owner",
  save: { status: number; body: unknown } = { status: 200, body: SETTINGS },
) {
  const fetchMock = stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser({ role }), csrf_token: "csrf" } }],
    ["/settings/communication", { status: 200, body: SETTINGS }],
    ["/settings/scheduling", { status: 200, body: SCHEDULING }],
  ]);
  render(
    <ProtectedLayout>
      <CommunicationSettingsPage />
    </ProtectedLayout>,
  );
  return { fetchMock, save };
}

test("owner sees the messaging configuration", async () => {
  renderSettings();
  expect(await screen.findByLabelText("Business display name")).toHaveValue("Acme Roofing");
  expect(screen.getByLabelText("Public form title")).toHaveValue("Request a quote");
  expect(screen.getByLabelText(/Send an automatic acknowledgment/)).not.toBeChecked();
  expect(screen.getByLabelText("Notification destination phone")).toHaveValue("");
  expect(screen.getByLabelText("First-response target (minutes)")).toHaveValue(5);
  expect(screen.getAllByText(/\{\{lead_name\}\}/).length).toBeGreaterThan(0);
});

test("non-owners are refused access", async () => {
  renderSettings("manager");
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You do not have access to communication settings.",
  );
  expect(screen.queryByLabelText("Business display name")).not.toBeInTheDocument();
});

test("saves changes with the CSRF token", async () => {
  const { fetchMock } = renderSettings();
  fireEvent.change(await screen.findByLabelText("Business display name"), {
    target: { value: "Acme Roofing and Gutters" },
  });
  fireEvent.click(screen.getByLabelText(/Send an automatic acknowledgment/));
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

  await waitFor(() => {
    const patch = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patch).toBeDefined();
    const body = JSON.parse(String((patch![1] as RequestInit).body));
    expect(body.business_name).toBe("Acme Roofing and Gutters");
    expect(body.acknowledgment_enabled).toBe(true);
    expect((patch![1] as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "csrf" });
  });
});

test("surfaces a server validation error", async () => {
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const url = String(args[0]);
    const init = args[1] as RequestInit | undefined;
    if (url.includes("/auth/session")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ user: makeUser({ role: "owner" }), csrf_token: "csrf" }),
      });
    }
    if (init?.method === "PATCH") {
      return Promise.resolve({
        ok: false,
        status: 400,
        json: () =>
          Promise.resolve({ detail: "Unknown template variables: secret_field" }),
      });
    }
    if (url.includes("/settings/scheduling")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(SCHEDULING) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(SETTINGS) });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <ProtectedLayout>
      <CommunicationSettingsPage />
    </ProtectedLayout>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Save settings" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("Unknown template variables"),
  );
});

test("owner sees the scheduling configuration alongside messaging", async () => {
  renderSettings();
  expect(await screen.findByLabelText("Business time zone")).toHaveValue("America/New_York");
  expect(screen.getByLabelText("Default appointment length (minutes)")).toHaveValue(60);
  expect(screen.getByLabelText("Buffer after an appointment (minutes)")).toHaveValue(15);
  expect(screen.getByLabelText(/Let customers book/)).toBeChecked();
  expect(screen.getByLabelText("Monday opens")).toHaveValue("09:00");
  // Closed days are shown as closed, not as an empty window.
  expect(screen.getByLabelText("Saturday", { selector: "input" })).not.toBeChecked();
  expect(screen.getAllByText(/\{\{appointment_date\}\}/).length).toBeGreaterThan(0);
});

test("saves scheduling changes, turning a closed day into an open window", async () => {
  const { fetchMock } = renderSettings();
  fireEvent.change(await screen.findByLabelText("Business time zone"), {
    target: { value: "America/Chicago" },
  });
  fireEvent.click(screen.getByLabelText("Saturday", { selector: "input" }));
  fireEvent.change(screen.getByLabelText("Saturday opens"), { target: { value: "10:00" } });
  fireEvent.click(screen.getByRole("button", { name: "Save scheduling settings" }));

  await waitFor(() => {
    const patch = fetchMock.mock.calls.find(
      ([url, init]) =>
        (init as RequestInit | undefined)?.method === "PATCH" &&
        String(url).includes("/settings/scheduling"),
    );
    expect(patch).toBeDefined();
    const body = JSON.parse(String((patch![1] as RequestInit).body));
    expect(body.business_timezone).toBe("America/Chicago");
    expect(body.business_hours.sat).toEqual([["10:00", "17:00"]]);
    expect(body.business_hours.sun).toEqual([]);
    expect((patch![1] as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "csrf" });
  });
});
