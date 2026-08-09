import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SmsPanel } from "@/components/sms-panel";
import type { Activity, Lead, OutboundMessage } from "@/lib/api";
import { stubFetchRoutes } from "@/test/helpers";

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
  first_inbound_at: "2026-08-09T12:00:00Z",
  response_due_at: "2026-08-09T12:05:00Z",
  first_response_at: null,
  first_response_seconds: null,
  response_target_met: null,
  response_overdue: false,
  created_at: "2026-08-09T12:00:00Z",
  updated_at: "2026-08-09T12:00:00Z",
  custom_values: {},
};

const INBOUND_SMS: Activity = {
  id: "bbbbbbbb-0000-0000-0000-000000000001",
  type: "inbound_request",
  channel: "sms",
  direction: "inbound",
  content: "Do you install metal roofs?",
  created_by_email: null,
  provider: "twilio",
  external_event_id: "SMfake1",
  occurred_at: "2026-08-09T12:00:00Z",
  meta: null,
  created_at: "2026-08-09T12:00:00Z",
};

function outbound(overrides: Partial<OutboundMessage> = {}): OutboundMessage {
  return {
    id: "cccccccc-0000-0000-0000-000000000001",
    lead_id: LEAD.id,
    purpose: "human_reply",
    to_phone: "+15550100001",
    body: "Yes, we do. When can we visit?",
    status: "delivered",
    provider_sid: "SMfake2",
    error_message: null,
    created_by_email: "owner@example.com",
    created_at: "2026-08-09T12:01:00Z",
    submitted_at: "2026-08-09T12:01:00Z",
    delivered_at: "2026-08-09T12:01:30Z",
    failed_at: null,
    ...overrides,
  };
}

function renderPanel(
  messages: OutboundMessage[],
  options: { lead?: Lead; canSend?: boolean; sendResponse?: { status: number; body: unknown } } = {},
) {
  const fetchMock = stubFetchRoutes([
    [
      `/leads/${LEAD.id}/messages`,
      options.sendResponse ?? { status: 200, body: messages },
    ],
  ]);
  // The GET listing and the POST send share a path, so serve the list first
  // and let assertions inspect the recorded calls.
  const view = render(
    <SmsPanel
      lead={options.lead ?? LEAD}
      activities={[INBOUND_SMS]}
      csrfToken="csrf-token"
      canSend={options.canSend ?? true}
      onChanged={() => {}}
    />,
  );
  return { fetchMock, view };
}

test("renders inbound and outbound messages in order with status", async () => {
  renderPanel([outbound()]);
  expect(await screen.findByText("Do you install metal roofs?")).toBeInTheDocument();
  expect(screen.getByText("Yes, we do. When can we visit?")).toBeInTheDocument();
  expect(screen.getByText(/Delivered/)).toBeInTheDocument();
  expect(screen.getByText(/owner@example\.com/)).toBeInTheDocument();
});

test("shows the composer with a character count for permitted users", async () => {
  renderPanel([]);
  const box = await screen.findByLabelText(/Send an SMS to \+15550100001/);
  fireEvent.change(box, { target: { value: "On our way" } });
  expect(screen.getByText("10 / 1600 characters")).toBeInTheDocument();
});

test("hides the composer from users who cannot send", async () => {
  renderPanel([], { canSend: false });
  await screen.findByText("Do you install metal roofs?");
  expect(screen.queryByRole("button", { name: "Send SMS" })).not.toBeInTheDocument();
});

test("blocks sending on archived leads", async () => {
  renderPanel([], { lead: { ...LEAD, archived_at: "2026-08-09T13:00:00Z" } });
  expect(await screen.findByText("Restore this lead to send messages.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Send SMS" })).not.toBeInTheDocument();
});

test("explains when the lead has no international phone number", async () => {
  renderPanel([], { lead: { ...LEAD, phone: "5550100001" } });
  expect(await screen.findByText(/international format/)).toBeInTheDocument();
});

test("sends with an idempotency key and prevents duplicate clicks", async () => {
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const init = args[1] as RequestInit | undefined;
    if (init?.method === "POST") {
      return new Promise((resolve) =>
        setTimeout(
          () =>
            resolve({
              ok: true,
              status: 201,
              json: () => Promise.resolve(outbound({ status: "submitted" })),
            }),
          20,
        ),
      );
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <SmsPanel
      lead={LEAD}
      activities={[]}
      csrfToken="csrf-token"
      canSend
      onChanged={() => {}}
    />,
  );
  const box = await screen.findByLabelText(/Send an SMS/);
  fireEvent.change(box, { target: { value: "On our way" } });
  const button = screen.getByRole("button", { name: "Send SMS" });
  fireEvent.click(button);
  fireEvent.click(button); // duplicate click while the first is in flight

  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Message sent."));
  const posts = fetchMock.mock.calls.filter(
    ([, init]) => (init as RequestInit | undefined)?.method === "POST",
  );
  expect(posts).toHaveLength(1);
  const headers = (posts[0][1] as RequestInit).headers as Record<string, string>;
  expect(headers["Idempotency-Key"]).toBeTruthy();
  expect(headers["X-CSRF-Token"]).toBe("csrf-token");
});

test("offers a retry for failed sends but warns instead for unknown outcomes", async () => {
  renderPanel([
    outbound({ status: "failed", error_message: "Unsubscribed recipient", delivered_at: null }),
    outbound({
      id: "cccccccc-0000-0000-0000-000000000002",
      status: "unknown",
      provider_sid: null,
      delivered_at: null,
    }),
  ]);
  expect(await screen.findByRole("button", { name: "Retry send" })).toBeInTheDocument();
  expect(screen.getByText(/Delivery was never confirmed/)).toBeInTheDocument();
  // Exactly one retry button: the unknown message deliberately has none.
  expect(screen.getAllByRole("button", { name: "Retry send" })).toHaveLength(1);
});

test("labels automated messages distinctly from human replies", async () => {
  renderPanel([
    outbound({ purpose: "auto_acknowledgment", body: "Thanks for contacting us." }),
  ]);
  expect(await screen.findByText(/auto acknowledgment/)).toBeInTheDocument();
});
