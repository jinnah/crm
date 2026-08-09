import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppointmentsPanel } from "@/components/appointments-panel";
import type { Appointment, AssignableUser, BookingLink, Lead } from "@/lib/api";
import { jsonResponse } from "@/test/helpers";

afterEach(() => {
  vi.unstubAllGlobals();
});

const LEAD: Lead = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  name: "Pat Customer",
  email: "pat@example.com",
  phone: "+15550100001",
  company: "",
  status: "new",
  source: "web_form",
  assigned_to: null,
  assignee_email: null,
  next_follow_up_at: null,
  last_contacted_at: null,
  needs_review: false,
  archived_at: null,
  first_inbound_at: null,
  response_due_at: null,
  first_response_at: null,
  first_response_seconds: null,
  response_target_met: null,
  response_overdue: false,
  created_at: "2026-08-09T12:00:00Z",
  updated_at: "2026-08-09T12:00:00Z",
  custom_values: {},
};

const USERS: AssignableUser[] = [
  { id: "11111111-1111-1111-1111-111111111111", email: "owner@example.com", role: "owner" },
  { id: "22222222-2222-2222-2222-222222222222", email: "tech@example.com", role: "team_member" },
];

const BASICS = {
  business_timezone: "UTC",
  appointment_duration_minutes: 60,
  min_booking_notice_minutes: 60,
  max_booking_days_ahead: 60,
  self_booking_enabled: true,
  business_hours: null,
};

const SLOTS = {
  date: "2026-08-20",
  timezone: "UTC",
  duration_minutes: 60,
  slots: ["2026-08-20T14:00:00Z", "2026-08-20T15:00:00Z"],
};

function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    id: "dddddddd-0000-0000-0000-000000000001",
    lead_id: LEAD.id,
    lead_name: "Pat Customer",
    assigned_to: USERS[1].id,
    assignee_email: "tech@example.com",
    subject: "Roof survey",
    notes: "",
    start_at: "2099-08-20T14:00:00Z",
    end_at: "2099-08-20T15:00:00Z",
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

type Answer = { status: number; body: unknown };

function stubPanel(options: {
  appointments?: Appointment[];
  link?: BookingLink | null;
  posts?: Record<string, Answer>;
}) {
  const posts = options.posts ?? {};
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const url = String(args[0]);
    const init = args[1] as RequestInit | undefined;
    if (init?.method === "POST") {
      const match = Object.keys(posts).find((fragment) => url.includes(fragment));
      const answer = match ? posts[match] : { status: 200, body: {} };
      return Promise.resolve(jsonResponse(answer.status, answer.body));
    }
    if (url.includes("/settings/scheduling-basics")) return Promise.resolve(jsonResponse(200, BASICS));
    if (url.includes("/appointments/availability")) return Promise.resolve(jsonResponse(200, SLOTS));
    if (url.includes("/booking-link")) return Promise.resolve(jsonResponse(200, options.link ?? null));
    if (url.includes("/appointments")) {
      return Promise.resolve(jsonResponse(200, options.appointments ?? []));
    }
    return Promise.resolve(jsonResponse(404, { detail: "Not found." }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPanel(
  options: Parameters<typeof stubPanel>[0] & { lead?: Lead; canSchedule?: boolean } = {},
) {
  const fetchMock = stubPanel(options);
  render(
    <AppointmentsPanel
      lead={options.lead ?? LEAD}
      csrfToken="csrf-token"
      canSchedule={options.canSchedule ?? true}
      users={USERS}
      onChanged={() => {}}
    />,
  );
  return fetchMock;
}

test("summarizes the next appointment and lists the history in the business zone", async () => {
  renderPanel({
    appointments: [
      appointment(),
      appointment({
        id: "dddddddd-0000-0000-0000-000000000002",
        subject: "Quote visit",
        status: "canceled",
        start_at: "2026-07-01T09:00:00Z",
        end_at: "2026-07-01T10:00:00Z",
        cancellation_reason: "Customer rescheduled by phone",
      }),
    ],
  });
  expect(await screen.findByRole("heading", { name: "Next appointment" })).toBeInTheDocument();
  expect(screen.getAllByText("Roof survey").length).toBeGreaterThan(0);
  expect(screen.getByText(/Canceled/)).toBeInTheDocument();
  expect(screen.getByText(/Customer rescheduled by phone/)).toBeInTheDocument();
  // Times carry the zone they were scheduled under, not the viewer's.
  expect(screen.getAllByText(/UTC/).length).toBeGreaterThan(0);
});

test("only a scheduled appointment offers dispositions", async () => {
  renderPanel({ appointments: [appointment({ status: "completed" })] });
  await screen.findByText(/Completed/);
  expect(screen.queryByRole("button", { name: "Cancel appointment" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reschedule" })).not.toBeInTheDocument();
  // History is still readable and downloadable.
  expect(screen.getByRole("button", { name: "Download .ics" })).toBeInTheDocument();
});

test("schedules the chosen free slot with the CSRF token", async () => {
  const fetchMock = renderPanel({
    posts: { "/appointments": { status: 201, body: appointment() } },
  });
  const radios = await screen.findAllByRole("radio");
  expect(radios).toHaveLength(2);
  fireEvent.click(radios[0]);
  fireEvent.click(screen.getByRole("button", { name: "Schedule appointment" }));

  await waitFor(() => {
    const post = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    );
    expect(post).toBeDefined();
    const [url, init] = post as [string, RequestInit];
    expect(String(url)).toContain(`/leads/${LEAD.id}/appointments`);
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-token");
    expect(JSON.parse(String(init.body)).start_at).toBe("2026-08-20T14:00:00Z");
  });
});

test("refuses to submit without a chosen time", async () => {
  const fetchMock = renderPanel({});
  fireEvent.click(await screen.findByRole("button", { name: "Schedule appointment" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose an available time first.");
  expect(
    fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST"),
  ).toHaveLength(0);
});

test("blocks scheduling on an archived lead but keeps the history", async () => {
  renderPanel({
    lead: { ...LEAD, archived_at: "2026-08-09T13:00:00Z" },
    appointments: [appointment()],
  });
  expect(
    await screen.findByText("Restore this lead to schedule appointments."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Schedule appointment" })).not.toBeInTheDocument();
  expect(screen.getAllByText("Roof survey").length).toBeGreaterThan(0);
});

test("shows a new booking link once and never keeps it in storage", async () => {
  const url = "http://localhost:3000/book/AbCdEf0123456789_-xyzTOKENvalue";
  renderPanel({
    posts: {
      "/booking-link": {
        status: 201,
        body: {
          id: "eeeeeeee-0000-0000-0000-000000000001",
          lead_id: LEAD.id,
          assigned_to: null,
          expires_at: "2026-08-23T12:00:00Z",
          revoked_at: null,
          duration_minutes: null,
          created_at: "2026-08-09T12:00:00Z",
          last_used_at: null,
          url,
        },
      },
    },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Create booking link" }));
  const field = await screen.findByLabelText("Booking link (shown once)");
  expect(field).toHaveValue(url);
  expect(screen.getByText(/shown only once/)).toBeInTheDocument();
  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
});

test("an existing link can be regenerated or revoked", async () => {
  const link: BookingLink = {
    id: "eeeeeeee-0000-0000-0000-000000000001",
    lead_id: LEAD.id,
    assigned_to: null,
    expires_at: "2026-08-23T12:00:00Z",
    revoked_at: null,
    duration_minutes: null,
    created_at: "2026-08-09T12:00:00Z",
    last_used_at: null,
    url: null,
  };
  const fetchMock = renderPanel({
    link,
    posts: { "/booking-link/revoke": { status: 200, body: link } },
  });
  expect(await screen.findByRole("button", { name: "Regenerate link" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Revoke link" }));
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Booking link revoked."),
  );
  expect(
    fetchMock.mock.calls.some(([url]) => String(url).includes("/booking-link/revoke")),
  ).toBe(true);
});

test("a user who cannot schedule sees history without controls", async () => {
  renderPanel({ canSchedule: false, appointments: [appointment()] });
  await screen.findAllByText("Roof survey");
  expect(screen.queryByRole("button", { name: "Schedule appointment" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create booking link" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel appointment" })).not.toBeInTheDocument();
});

test("the calendar file is fetched inside the session, not linked publicly", async () => {
  const blob = new Blob(["BEGIN:VCALENDAR"], { type: "text/calendar" });
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const url = String(args[0]);
    if (url.includes("calendar.ics")) {
      return Promise.resolve({ ok: true, status: 200, blob: () => Promise.resolve(blob) });
    }
    if (url.includes("/settings/scheduling-basics")) return Promise.resolve(jsonResponse(200, BASICS));
    if (url.includes("/appointments/availability")) return Promise.resolve(jsonResponse(200, SLOTS));
    if (url.includes("/booking-link")) return Promise.resolve(jsonResponse(200, null));
    return Promise.resolve(jsonResponse(200, [appointment()]));
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:test", revokeObjectURL: () => {} });

  render(
    <AppointmentsPanel
      lead={LEAD}
      csrfToken="csrf-token"
      canSchedule
      users={USERS}
      onChanged={() => {}}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Download .ics" }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("calendar.ics"));
    expect(call).toBeDefined();
    expect((call![1] as RequestInit).credentials).toBe("include");
  });
});
