import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import PublicAppointmentPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ token: "AbCdEf0123456789_-xyzMANAGEtoken" }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

const APPOINTMENT = {
  business_name: "Acme Plumbing",
  staff_display_name: "Sam",
  booking_reference: "APT-ABCD1234",
  start_at: "2026-08-20T14:00:00Z",
  end_at: "2026-08-20T15:00:00Z",
  timezone: "UTC",
  status: "scheduled",
  can_change: true,
  days: [
    {
      date: "2026-08-21",
      timezone: "UTC",
      duration_minutes: 60,
      slots: ["2026-08-21T09:00:00Z", "2026-08-21T10:00:00Z"],
    },
  ],
};

function stubManage(
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

test("shows the appointment without any CRM detail behind it", async () => {
  const fetchMock = stubManage({ status: 200, body: APPOINTMENT });
  render(<PublicAppointmentPage />);
  expect(
    await screen.findByRole("heading", { name: "Your appointment with Acme Plumbing" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/APT-ABCD1234/)).toBeInTheDocument();
  const urls = fetchMock.mock.calls.map(([target]) => String(target));
  expect(urls).toContain("/api/public-appointment/AbCdEf0123456789_-xyzMANAGEtoken");
  const markup = document.body.innerHTML;
  expect(markup).not.toMatch(/lead_id|notes|@example/i);
});

test("cancels through the capability, sending no identifier", async () => {
  const fetchMock = stubManage({ status: 200, body: APPOINTMENT }, [
    { status: 200, body: { ...APPOINTMENT, status: "canceled", can_change: false, days: [] } },
  ]);
  render(<PublicAppointmentPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Cancel appointment" }));

  await waitFor(() =>
    expect(screen.getByText("This appointment is canceled. Contact us if you would like a new time."))
      .toBeInTheDocument(),
  );
  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST")!;
  expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ action: "cancel" });
  // A canceled appointment offers no further changes.
  expect(screen.queryByRole("button", { name: "Change time" })).not.toBeInTheDocument();
});

test("moves to another offered time", async () => {
  const moved = {
    ...APPOINTMENT,
    start_at: "2026-08-21T09:00:00Z",
    end_at: "2026-08-21T10:00:00Z",
    days: [],
  };
  const fetchMock = stubManage({ status: 200, body: APPOINTMENT }, [{ status: 200, body: moved }]);
  render(<PublicAppointmentPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Change time" }));
  // The label is formatted for the reader's locale, so select by position.
  fireEvent.click((await screen.findAllByRole("radio"))[0]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm new time" }));

  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Your appointment has been moved."),
  );
  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST")!;
  const body = JSON.parse(String((post[1] as RequestInit).body));
  expect(body).toMatchObject({ action: "reschedule", start_at: "2026-08-21T09:00:00Z" });
  expect(body).not.toHaveProperty("appointment_id");
});

test("asks for a time before moving", async () => {
  const fetchMock = stubManage({ status: 200, body: APPOINTMENT });
  render(<PublicAppointmentPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Change time" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm new time" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a new time first.");
  expect(
    fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST"),
  ).toHaveLength(0);
});

test("a taken slot is reported without losing the appointment", async () => {
  stubManage({ status: 200, body: APPOINTMENT }, [
    { status: 409, body: { detail: "That time is no longer available." } },
  ]);
  render(<PublicAppointmentPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Change time" }));
  fireEvent.click((await screen.findAllByRole("radio"))[1]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm new time" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("no longer available"),
  );
  expect(screen.getByText(/APT-ABCD1234/)).toBeInTheDocument();
});

test("an unusable capability says so plainly", async () => {
  stubManage({ status: 404, body: { detail: "This appointment link is not valid." } });
  render(<PublicAppointmentPage />);
  expect(
    await screen.findByRole("heading", { name: "Appointment unavailable" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("not valid");
});
