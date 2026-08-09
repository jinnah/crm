import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../../layout";
import LeadDetailPage from "./page";

const LEAD_ID = "aaaaaaaa-0000-0000-0000-000000000001";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useParams: () => ({ id: LEAD_ID }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

const LEAD = {
  id: LEAD_ID,
  name: "Alpha Roofing",
  email: "alpha@example.com",
  phone: "+15550100001",
  company: "Alpha LLC",
  status: "new",
  source: "web_form",
  assigned_to: null,
  assignee_email: null,
  next_follow_up_at: null,
  last_contacted_at: null,
  needs_review: false,
  archived_at: null,
  created_at: "2026-07-01T12:00:00Z",
  updated_at: "2026-07-01T12:00:00Z",
  custom_values: {},
};

const ACTIVITIES = [
  {
    id: "cccccccc-0000-0000-0000-000000000001",
    type: "inbound_request",
    channel: "web_form",
    direction: "inbound",
    content: "Roof quote request\n\nMy roof is leaking.",
    created_by_email: null,
    provider: "n8n-webform",
    external_event_id: "form-1",
    occurred_at: "2026-07-01T11:59:00Z",
    meta: null,
    created_at: "2026-07-01T12:00:00Z",
  },
  {
    id: "cccccccc-0000-0000-0000-000000000002",
    type: "created",
    channel: null,
    direction: null,
    content: "Lead created.",
    created_by_email: "owner@example.com",
    provider: null,
    external_event_id: null,
    occurred_at: null,
    meta: null,
    created_at: "2026-07-01T12:00:00Z",
  },
];

function renderDetail() {
  const fetchMock = stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser(), csrf_token: "csrf-token" } }],
    [`/leads/${LEAD_ID}/activities`, { status: 200, body: ACTIVITIES }],
    [`/leads/${LEAD_ID}/notes`, { status: 201, body: ACTIVITIES[1] }],
    [`/leads/${LEAD_ID}`, { status: 200, body: LEAD }],
    ["/custom-fields", { status: 200, body: [] }],
    ["/leads/assignable-users", { status: 200, body: [] }],
  ]);
  const view = render(
    <ProtectedLayout>
      <LeadDetailPage />
    </ProtectedLayout>,
  );
  return { fetchMock, view };
}

test("renders lead details and the activity timeline", async () => {
  renderDetail();
  expect(await screen.findByRole("heading", { name: /Alpha Roofing/ })).toBeInTheDocument();
  expect(screen.getByLabelText("Status")).toHaveValue("new");
  expect(screen.getByText("Inbound request")).toBeInTheDocument();
  expect(screen.getByText(/My roof is leaking/)).toBeInTheDocument();
  expect(screen.getByText("Lead created.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
});

test("adds an internal note with the CSRF token", async () => {
  const { fetchMock } = renderDetail();
  fireEvent.change(await screen.findByLabelText("Internal note"), {
    target: { value: "Called and left a voicemail" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add note" }));
  await waitFor(() => {
    const noteCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/notes"));
    expect(noteCall).toBeDefined();
    expect((noteCall![1] as RequestInit).method).toBe("POST");
    expect((noteCall![1] as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "csrf-token" });
    expect(JSON.parse(String((noteCall![1] as RequestInit).body))).toEqual({
      content: "Called and left a voicemail",
    });
  });
});
