import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "./layout";
import HomePage from "./page";

vi.mock("next/navigation", () => ({
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

test("shows attention groups with leads", async () => {
  renderHome({
    overdue: [LEAD],
    due_today: [],
    unassigned: [{ ...LEAD, id: "bbbbbbbb-0000-0000-0000-000000000002", name: "Fresh Lead" }],
    needs_review: [],
    unresponded: [],
  });
  expect(await screen.findByRole("heading", { name: "Overdue follow-ups" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Overdue Lead" })).toHaveAttribute(
    "href",
    `/leads/${LEAD.id}`,
  );
  expect(screen.getByRole("heading", { name: "New unassigned leads" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Fresh Lead" })).toBeInTheDocument();
  // Empty groups are not rendered.
  expect(screen.queryByRole("heading", { name: "Needs review" })).not.toBeInTheDocument();
});

test("shows the empty state when nothing needs attention", async () => {
  renderHome({ overdue: [], due_today: [], unassigned: [], needs_review: [], unresponded: [] });
  expect(await screen.findByText("Nothing needs attention right now.")).toBeInTheDocument();
});
