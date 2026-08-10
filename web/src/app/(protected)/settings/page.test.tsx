import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../layout";
import SettingsPage from "./page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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

const BRANDING = {
  business_name: "Acme Roofing",
  has_logo: false,
  width: null,
  height: null,
  updated_at: null,
  initials: "AR",
};

const VOICE = {
  voice_ack_enabled: false,
  voice_ack_template: "Thanks for calling {{business_name}}, {{lead_name}}.",
  voice_alert_enabled: false,
  voice_alert_template: "Voice lead: {{lead_name}} — {{call_summary}}",
  voice_alert_recipients: "business",
  voice_default_staff_id: null,
  voice_transcript_retention_enabled: false,
  voice_transcript_retention_days: 30,
};

const STAFF = [
  {
    id: "22222222-2222-2222-2222-222222222222",
    email: "tech@example.com",
    role: "team_member",
    display_name: "Sam Field",
  },
];

function renderSettings(role: "owner" | "manager" = "owner") {
  const fetchMock = stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser({ role }), csrf_token: "csrf" } }],
    ["/settings/communication", { status: 200, body: SETTINGS }],
    ["/settings/scheduling", { status: 200, body: SCHEDULING }],
    ["/settings/branding", { status: 200, body: BRANDING }],
    ["/settings/voice", { status: 200, body: VOICE }],
    ["/leads/assignable-users", { status: 200, body: STAFF }],
  ]);
  render(
    <ProtectedLayout>
      <SettingsPage />
    </ProtectedLayout>,
  );
  return fetchMock;
}

function patchCalls(fetchMock: ReturnType<typeof renderSettings>, fragment: string) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).includes(fragment) && (init as RequestInit | undefined)?.method === "PATCH",
  );
}

test("owner lands on Business & branding with the section navigation", async () => {
  renderSettings();
  expect(await screen.findByLabelText("Business display name")).toHaveValue("Acme Roofing");
  // All sections are reachable from one navigator.
  const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
  expect(tabs).toEqual([
    "Business & branding",
    "Lead intake",
    "Automated messages",
    "Response targets",
    "Scheduling rules",
    "Availability",
    "Appointment notifications",
    "Voice calls",
    "Documents & email",
  ]);
  // The branding manager is on the first section.
  expect(await screen.findByText(/Drag an image here/)).toBeInTheDocument();
  // Scheduling fields are not on this section.
  expect(screen.queryByLabelText("Business time zone")).not.toBeInTheDocument();
});

test("non-owners are refused access", async () => {
  renderSettings("manager");
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You do not have access to settings.",
  );
  expect(screen.queryByLabelText("Business display name")).not.toBeInTheDocument();
});

test("saving a section sends only its fields, with the CSRF token", async () => {
  const fetchMock = renderSettings();
  const field = await screen.findByLabelText("Business display name");
  const save = screen.getByRole("button", { name: "Save business profile" });
  expect(save).toBeDisabled(); // nothing changed yet

  fireEvent.change(field, { target: { value: "Acme Roofing and Gutters" } });
  expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save business profile" }));

  await waitFor(() => {
    const patches = patchCalls(fetchMock, "/settings/communication");
    expect(patches).toHaveLength(1);
    const [, init] = patches[0];
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body).toEqual({ business_name: "Acme Roofing and Gutters" });
    expect((init as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "csrf" });
  });
});

test("switching sections with unsaved changes asks for confirmation", async () => {
  renderSettings();
  const field = await screen.findByLabelText("Business display name");
  fireEvent.change(field, { target: { value: "Edited" } });

  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("tab", { name: "Scheduling rules" }));
  expect(confirm).toHaveBeenCalled();
  // Declining stays on the section with the edit intact.
  expect(screen.getByLabelText("Business display name")).toHaveValue("Edited");

  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("tab", { name: "Scheduling rules" }));
  expect(await screen.findByLabelText("Business time zone")).toHaveValue("America/New_York");
});

test("template sections disable their editors until the toggle is on", async () => {
  renderSettings();
  await screen.findByLabelText("Business display name");
  fireEvent.click(screen.getByRole("tab", { name: "Automated messages" }));

  const template = await screen.findByLabelText("Acknowledgment message");
  expect(template).toBeDisabled(); // acknowledgment_enabled is false
  fireEvent.click(screen.getByLabelText(/Send an automatic acknowledgment/));
  expect(screen.getByLabelText("Acknowledgment message")).toBeEnabled();
  // Variables are offered as insertable chips with a character counter.
  expect(screen.getAllByRole("button", { name: "{{lead_name}}" }).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/\/ 1600/).length).toBeGreaterThan(0);
});

test("a server validation error is surfaced on save", async () => {
  const fetchMock = renderSettings();
  const field = await screen.findByLabelText("Business display name");

  // From here on, every PATCH is rejected while reads keep working.
  const passthrough = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((...args: Parameters<typeof fetch>) => {
    const init = args[1] as RequestInit | undefined;
    if (init?.method === "PATCH") {
      return Promise.resolve({
        ok: false,
        status: 400,
        json: () => Promise.resolve({ detail: "Unknown template variables: secret_field" }),
      });
    }
    return passthrough(...args);
  });

  fireEvent.change(field, { target: { value: "Changed" } });
  fireEvent.click(screen.getByRole("button", { name: "Save business profile" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("Unknown template variables"),
  );
});

test("the voice section saves its settings and offers staff by display name", async () => {
  const fetchMock = renderSettings();
  await screen.findByLabelText("Business display name");
  fireEvent.click(screen.getByRole("tab", { name: "Voice calls" }));

  // Transcript retention is presented off by default with its day count gated.
  const retention = await screen.findByLabelText(/Keep full call transcripts/);
  expect(retention).not.toBeChecked();
  expect(screen.getByLabelText("Delete transcripts after (days)")).toBeDisabled();

  // Staff options use the display name, not the email address.
  const staffSelect = screen.getByLabelText("Default staff member for phone bookings");
  expect(staffSelect).toHaveTextContent("Sam Field");
  expect(staffSelect).not.toHaveTextContent("tech@example.com");

  fireEvent.click(screen.getByLabelText(/Text the caller an acknowledgment/));
  fireEvent.click(screen.getByRole("button", { name: "Save voice call settings" }));

  await waitFor(() => {
    const patches = patchCalls(fetchMock, "/settings/voice");
    expect(patches).toHaveLength(1);
    const body = JSON.parse(String((patches[0][1] as RequestInit).body));
    expect(body.voice_ack_enabled).toBe(true);
    // No configured staff member: clearing travels as the explicit flag.
    expect(body.clear_default_staff).toBe(true);
    expect(body).not.toHaveProperty("voice_default_staff_id");
  });
});

test("availability edits save through the weekday editor", async () => {
  const fetchMock = renderSettings();
  await screen.findByLabelText("Business display name");
  fireEvent.click(screen.getByRole("tab", { name: "Availability" }));

  // Saturday is closed; opening it produces a real window, not raw text.
  const saturday = await screen.findByLabelText("Saturday", { selector: "input" });
  expect(saturday).not.toBeChecked();
  fireEvent.click(saturday);
  fireEvent.change(screen.getByLabelText("Saturday opens"), { target: { value: "10:00" } });
  fireEvent.click(screen.getByRole("button", { name: "Save availability" }));

  await waitFor(() => {
    const patches = patchCalls(fetchMock, "/settings/scheduling");
    expect(patches).toHaveLength(1);
    const body = JSON.parse(String((patches[0][1] as RequestInit).body));
    expect(Object.keys(body)).toEqual(["business_hours"]);
    expect(body.business_hours.sat).toEqual([["10:00", "17:00"]]);
    expect(body.business_hours.sun).toEqual([]);
    expect(body.business_hours.mon).toEqual([["09:00", "17:00"]]);
  });
});
