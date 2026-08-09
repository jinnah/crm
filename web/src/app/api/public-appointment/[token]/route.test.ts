import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { GET, POST } from "./route";

const ORIGINAL_ENV = { ...process.env };
const TOKEN = "AbCdEf0123456789_-xyzMANAGEtoken";

beforeEach(() => {
  process.env.CRM_API_URL = "http://api:8000";
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  vi.unstubAllGlobals();
});

function stubForward(status: number, body: unknown) {
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    void args;
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function context(token = TOKEN) {
  return { params: Promise.resolve({ token }) };
}

function postRequest(payload: unknown, ip = "203.0.113.50", contentType = "application/json") {
  return new Request(`http://localhost:3000/api/public-appointment/${TOKEN}`, {
    method: "POST",
    headers: { "Content-Type": contentType, "x-forwarded-for": ip },
    body: JSON.stringify(payload),
  });
}

test("reads the appointment through the server with no caching", async () => {
  const fetchMock = stubForward(200, { booking_reference: "APT-1", status: "scheduled" });
  const response = await GET(new Request("http://localhost:3000/x"), context());
  expect(response.status).toBe(200);
  expect(fetchMock.mock.calls[0][0]).toBe(`http://api:8000/api/v1/public/appointments/${TOKEN}`);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
});

test("refuses a malformed capability without contacting the CRM", async () => {
  const fetchMock = stubForward(200, {});
  for (const bad of ["short", "spaces here now ok", "../../secrets"]) {
    expect((await GET(new Request("http://localhost:3000/x"), context(bad))).status).toBe(404);
    expect((await POST(postRequest({ action: "cancel" }), context(bad))).status).toBe(404);
  }
  expect(fetchMock).not.toHaveBeenCalled();
});

test("cancelling forwards the capability and nothing else", async () => {
  const fetchMock = stubForward(200, { status: "canceled" });
  const response = await POST(
    postRequest({ action: "cancel", appointment_id: "aaaa", lead_id: "bbbb" }),
    context(),
  );
  expect(response.status).toBe(200);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe(`http://api:8000/api/v1/public/appointments/${TOKEN}/cancel`);
  expect(init?.body).toBeUndefined();
});

test("rescheduling forwards only the new time and the honeypot", async () => {
  const fetchMock = stubForward(200, { status: "scheduled" });
  await POST(
    postRequest({
      action: "reschedule",
      start_at: "2026-08-21T09:00:00Z",
      lead_id: "aaaaaaaa-0000-0000-0000-000000000001",
      status: "completed",
    }),
    context(),
  );
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe(`http://api:8000/api/v1/public/appointments/${TOKEN}/reschedule`);
  expect(Object.keys(JSON.parse(String(init?.body))).sort()).toEqual(["start_at", "website"]);
});

test("rejects an unknown action, a wrong content type and an oversized body", async () => {
  const fetchMock = stubForward(200, {});
  expect((await POST(postRequest({ action: "delete" }, "203.0.113.51"), context())).status).toBe(
    422,
  );
  expect(
    (await POST(postRequest({ action: "cancel" }, "203.0.113.52", "text/plain"), context())).status,
  ).toBe(415);

  const huge = new Request(`http://localhost:3000/api/public-appointment/${TOKEN}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-forwarded-for": "203.0.113.53" },
    body: JSON.stringify({ action: "reschedule", start_at: "x".repeat(8 * 1024) }),
  });
  expect((await POST(huge, context())).status).toBe(413);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("throttles repeated attempts from one address", async () => {
  stubForward(200, { status: "canceled" });
  const statuses: number[] = [];
  for (let index = 0; index < 25; index += 1) {
    statuses.push((await POST(postRequest({ action: "cancel" }, "203.0.113.95"), context())).status);
  }
  expect(statuses.filter((status) => status === 429).length).toBeGreaterThan(0);
});

test("never logs the capability when forwarding fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("ECONNREFUSED"))),
  );
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const response = await POST(postRequest({ action: "cancel" }, "203.0.113.54"), context());
  expect(response.status).toBe(502);
  expect(warn.mock.calls.flat().join(" ")).not.toContain(TOKEN);
  warn.mockRestore();
});
