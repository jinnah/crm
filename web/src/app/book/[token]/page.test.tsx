import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import PublicBookingPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ token: "AbCdEf0123456789_-xyzTOKENvalue" }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

const INFO = {
  business_name: "Acme Plumbing",
  intro: "Pick a time that suits you.",
  staff_display_name: "Sam",
  duration_minutes: 60,
  timezone: "UTC",
  days: [
    {
      date: "2026-08-20",
      timezone: "UTC",
      duration_minutes: 60,
      slots: ["2026-08-20T14:00:00Z", "2026-08-20T15:00:00Z"],
    },
  ],
};

/** GET serves the page; each POST is answered from the queue in order. */
function stubBooking(
  info: { status: number; body: unknown },
  posts: Array<{ status: number; body: unknown }> = [],
) {
  const remaining = [...posts];
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const init = args[1] as RequestInit | undefined;
    const answer = init?.method === "POST" ? (remaining.shift() ?? { status: 500, body: {} }) : info;
    return Promise.resolve({
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json: () => Promise.resolve(answer.body),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("offers the free times and nothing about the lead", async () => {
  const fetchMock = stubBooking({ status: 200, body: INFO });
  render(<PublicBookingPage />);
  expect(await screen.findByRole("heading", { name: "Book with Acme Plumbing" })).toBeInTheDocument();
  expect(screen.getByText(/60 minutes/)).toBeInTheDocument();
  expect(screen.getAllByRole("radio")).toHaveLength(2);

  // Only the same-origin route is ever contacted — the CRM is not exposed.
  expect(String(fetchMock.mock.calls[0][0])).toBe(
    "/api/public-booking/AbCdEf0123456789_-xyzTOKENvalue",
  );
  const markup = document.body.innerHTML;
  expect(markup).not.toMatch(/lead|customer_id|phone|@/i);
});

test("books the chosen time and shows the reference", async () => {
  const fetchMock = stubBooking({ status: 200, body: INFO }, [
    {
      status: 200,
      body: {
        booking_reference: "APT-ABCD1234",
        start_at: "2026-08-20T14:00:00Z",
        end_at: "2026-08-20T15:00:00Z",
        timezone: "UTC",
      },
    },
  ]);
  render(<PublicBookingPage />);
  // The label is formatted for the reader's locale, so select by position.
  fireEvent.click((await screen.findAllByRole("radio"))[0]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm booking" }));

  expect(await screen.findByText("APT-ABCD1234")).toBeInTheDocument();
  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST")!;
  const body = JSON.parse(String((post[1] as RequestInit).body));
  expect(body.start_at).toBe("2026-08-20T14:00:00Z");
  expect(body.booking_key.length).toBeGreaterThanOrEqual(8);
});

test("asks for a time before booking", async () => {
  const fetchMock = stubBooking({ status: 200, body: INFO });
  render(<PublicBookingPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Confirm booking" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a time first.");
  expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST")).toHaveLength(
    0,
  );
});

test("a retry after a failure reuses the same booking key", async () => {
  const fetchMock = stubBooking({ status: 200, body: INFO }, [
    { status: 502, body: { detail: "We could not reach the booking service." } },
    {
      status: 200,
      body: {
        booking_reference: "APT-RETRY01",
        start_at: "2026-08-20T14:00:00Z",
        end_at: "2026-08-20T15:00:00Z",
        timezone: "UTC",
      },
    },
  ]);
  render(<PublicBookingPage />);
  // The label is formatted for the reader's locale, so select by position.
  fireEvent.click((await screen.findAllByRole("radio"))[0]);
  const confirm = screen.getByRole("button", { name: "Confirm booking" });
  fireEvent.click(confirm);
  expect(await screen.findByRole("alert")).toHaveTextContent("could not reach");

  fireEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
  expect(await screen.findByText("APT-RETRY01")).toBeInTheDocument();

  const posts = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST");
  expect(posts).toHaveLength(2);
  const keys = posts.map(([, init]) => JSON.parse(String((init as RequestInit).body)).booking_key);
  // The same key means the CRM returns the original appointment, not a second one.
  expect(keys[0]).toBe(keys[1]);
});

test("tells the customer plainly when the link no longer works", async () => {
  stubBooking({ status: 410, body: { detail: "This booking link has been withdrawn." } });
  render(<PublicBookingPage />);
  expect(await screen.findByRole("heading", { name: "Booking unavailable" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("withdrawn");
});

test("reports a rejected slot without losing the page", async () => {
  stubBooking({ status: 200, body: INFO }, [
    { status: 409, body: { detail: "That time is no longer available." } },
  ]);
  render(<PublicBookingPage />);
  fireEvent.click((await screen.findAllByRole("radio"))[1]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("no longer available"),
  );
  expect(screen.getAllByRole("radio")).toHaveLength(2);
});

test("keeps no booking token or customer detail in browser storage", async () => {
  stubBooking({ status: 200, body: INFO }, [
    {
      status: 200,
      body: {
        booking_reference: "APT-STORE01",
        start_at: "2026-08-20T14:00:00Z",
        end_at: "2026-08-20T15:00:00Z",
        timezone: "UTC",
        manage_token: "manage-token-value",
      },
    },
  ]);
  render(<PublicBookingPage />);
  // The label is formatted for the reader's locale, so select by position.
  fireEvent.click((await screen.findAllByRole("radio"))[0]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
  await screen.findByText("APT-STORE01");

  // The capability is offered as a link to keep, never written to storage or
  // a cookie where a later visitor could pick it up.
  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
  expect(document.cookie).toBe("");
  expect(screen.getByRole("link", { name: /Change or cancel/ })).toHaveAttribute(
    "href",
    "/appointment/manage-token-value",
  );
});
