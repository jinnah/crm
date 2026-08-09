import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../layout";
import LeadsPage from "./page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

const LEADS = {
  items: [
    {
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      name: "Alpha Roofing",
      email: "alpha@example.com",
      phone: "+15550100001",
      company: "Alpha LLC",
      status: "contacted",
      source: "web_form",
      assigned_to: null,
      assignee_email: "member@example.com",
      next_follow_up_at: null,
      last_contacted_at: null,
      needs_review: true,
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
    },
  ],
  total: 1,
  page: 1,
  page_size: 25,
};

function renderLeads(role: "owner" | "team_member") {
  stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser({ role }), csrf_token: "csrf" } }],
    ["/leads/assignable-users", { status: 200, body: [] }],
    ["/leads?", { status: 200, body: LEADS }],
  ]);
  return render(
    <ProtectedLayout>
      <LeadsPage />
    </ProtectedLayout>,
  );
}

test("owner sees leads, filters, badges and the new-lead action", async () => {
  renderLeads("owner");
  // The lead renders twice: once in the desktop table, once as the stacked
  // mobile card. CSS shows exactly one at a time.
  expect((await screen.findAllByRole("link", { name: /Alpha Roofing/ })).length).toBe(2);
  expect(screen.getAllByText("Needs review").length).toBeGreaterThan(1); // filter + badge
  expect(screen.getAllByText("member@example.com").length).toBeGreaterThan(0);
  expect(screen.getByRole("link", { name: "New lead" })).toBeInTheDocument();
  expect(screen.getByLabelText("Search")).toBeInTheDocument();
  expect(screen.getByLabelText("Status")).toBeInTheDocument();
  expect(screen.getByLabelText("Assignee")).toBeInTheDocument();
  expect(screen.getByText(/Page 1 of 1/)).toBeInTheDocument();
});

test("team members get no create action or assignee filter", async () => {
  renderLeads("team_member");
  expect((await screen.findAllByRole("link", { name: /Alpha Roofing/ })).length).toBe(2);
  expect(screen.queryByRole("link", { name: "New lead" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Assignee")).not.toBeInTheDocument();
});
