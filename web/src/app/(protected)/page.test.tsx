import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "./layout";
import HomePage from "./page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

const LEAD = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  name: "Overdue Lead",
  email: "lead@example.com",
  phone: null,
  company: "",
  status: "new",
  source: "web_form",
  assigned_to: null,
  assignee_email: null,
  next_follow_up_at: "2026-08-01T12:00:00Z",
  last_contacted_at: null,
  needs_review: false,
  archived_at: null,
  first_inbound_at: null,
  response_due_at: null,
  first_response_at: null,
  first_response_seconds: null,
  response_target_met: null,
  response_overdue: false,
  created_at: "2026-07-01T12:00:00Z",
  updated_at: "2026-07-01T12:00:00Z",
  custom_values: {},
};

const APPOINTMENT = {
  id: "dddddddd-0000-0000-0000-000000000001",
  lead_id: LEAD.id,
  lead_name: "Overdue Lead",
  subject: "Roof survey",
  start_at: "2026-08-10T14:00:00Z",
  timezone: "UTC",
  status: "scheduled",
  detail: null,
};

const EMPTY_QUEUE = {
  overdue: [],
  due_today: [],
  unassigned: [],
  needs_review: [],
  unresponded: [],
  appointments_overdue: [],
  appointments_upcoming: [],
  appointment_messages_failed: [],
  appointment_messages_unknown: [],
};

function renderHome(queue: unknown, role: "owner" | "team_member" = "owner") {
  stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser({ role }), csrf_token: "csrf" } }],
    ["/leads/attention", { status: 200, body: queue }],
  ]);
  return render(
    <ProtectedLayout>
      <HomePage />
    </ProtectedLayout>,
  );
}

test("shows summary cards and grouped attention sections", async () => {
  renderHome({
    ...EMPTY_QUEUE,
    overdue: [LEAD],
    unassigned: [{ ...LEAD, id: "bbbbbbbb-0000-0000-0000-000000000002", name: "Fresh Lead" }],
    appointments_upcoming: [APPOINTMENT],
    appointment_messages_unknown: [
      { ...APPOINTMENT, detail: "confirmation message unknown: unconfirmed" },
    ],
  });
  expect(await screen.findByRole("heading", { name: "Today" })).toBeInTheDocument();

  // Summary cards carry real counts, not invented metrics. "Follow-ups due"
  // appears twice by design: as the summary card and as its section heading.
  expect(screen.getAllByText("Follow-ups due").length).toBe(2);
  expect(screen.getByText("Unassigned leads")).toBeInTheDocument();
  expect(screen.getByText("Upcoming appointments")).toBeInTheDocument();

  // Grouped sections with rows that link to the lead.
  expect(screen.getByRole("heading", { name: /Follow-ups due/ })).toBeInTheDocument();
  // The same lead can legitimately appear in several categories; every row
  // links to the same place.
  const overdueLinks = screen.getAllByRole("link", { name: "Overdue Lead" });
  expect(overdueLinks.length).toBeGreaterThan(0);
  for (const link of overdueLinks) {
    expect(link).toHaveAttribute("href", `/leads/${LEAD.id}`);
  }
  expect(screen.getByRole("heading", { name: /New unassigned leads/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Fresh Lead" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Coming up/ })).toBeInTheDocument();
  // Ambiguous provider outcomes stay visible but concise.
  expect(
    screen.getByRole("heading", { name: /Appointment messages to check/ }),
  ).toBeInTheDocument();
  expect(screen.getByText("Unconfirmed")).toBeInTheDocument();
  // Empty categories are not rendered as sections.
  expect(screen.queryByRole("heading", { name: /Response overdue/ })).not.toBeInTheDocument();
});

test("shows an intentional empty state when nothing needs attention", async () => {
  renderHome(EMPTY_QUEUE);
  expect(await screen.findByText("All clear")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to leads" })).toBeInTheDocument();
});

test("relative urgency accompanies the exact follow-up time", async () => {
  renderHome({ ...EMPTY_QUEUE, overdue: [LEAD] });
  expect(await screen.findByText(/Overdue ·/)).toBeInTheDocument();
});

test("team members do not see the unassigned category", async () => {
  renderHome(
    {
      ...EMPTY_QUEUE,
      unassigned: [{ ...LEAD, id: "cccccccc-0000-0000-0000-000000000003", name: "Hidden Lead" }],
    },
    "team_member",
  );
  expect(await screen.findByText("All clear")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Hidden Lead" })).not.toBeInTheDocument();
});
