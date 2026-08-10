import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { GET, POST } from "./route";

const ORIGINAL_ENV = { ...process.env };
const TOKEN = "AbCdEf0123456789_-xyzTOKENvalue";

beforeEach(() => {
  process.env.CRM_API_URL = "http://api:8000";
  process.env.INTERNAL_BFF_KEY = "internal-test-key";
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

function postRequest(payload: unknown, ip = "203.0.113.30", contentType = "application/json") {
  return new Request(`http://localhost:3000/api/public-booking/${TOKEN}`, {
    method: "POST",
    headers: { "Content-Type": contentType, "x-forwarded-for": ip },
    body: JSON.stringify(payload),
  });
}

const VALID = {
  start_at: "2026-08-20T14:00:00Z",
  booking_key: "booking-key-abcdef",
};

test("fetches the booking page through the server, never from the browser", async () => {
  const fetchMock = stubForward(200, { business_name: "Acme", days: [] });
  const response = await GET(new Request("http://localhost:3000/x"), context());
  expect(response.status).toBe(200);
  // Fixed internal path: the capability travels in the body, never the URL.
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://api:8000/api/v1/internal/booking/info");
  expect(String(url)).not.toContain(TOKEN);
  expect(init?.method).toBe("POST");
  expect(new Headers(init?.headers).get("X-Internal-Key")).toBe("internal-test-key");
  expect(JSON.parse(String(init?.body))).toMatchObject({ token: TOKEN });
  // Nothing is cached, so a revoked link cannot be served from a stale copy.
  expect(response.headers.get("Cache-Control")).toBe("no-store");
});

test("refuses a malformed token without contacting the CRM", async () => {
  const fetchMock = stubForward(200, {});
  for (const bad of ["short", "has spaces in it here", "../../etc/passwd"]) {
    const response = await GET(new Request("http://localhost:3000/x"), context(bad));
    expect(response.status).toBe(404);
  }
  expect(fetchMock).not.toHaveBeenCalled();
});

test("forwards only the time, the booking key and the honeypot", async () => {
  const fetchMock = stubForward(200, { booking_reference: "APT-ABCD1234" });
  const response = await POST(
    postRequest({
      ...VALID,
      // A client that invents authority gets it stripped here.
      lead_id: "aaaaaaaa-0000-0000-0000-000000000001",
      assigned_to: "bbbbbbbb-0000-0000-0000-000000000002",
      status: "completed",
    }),
    context(),
  );
  expect(response.status).toBe(200);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://api:8000/api/v1/internal/booking/confirm");
  const forwarded = JSON.parse(String(init?.body));
  expect(Object.keys(forwarded).sort()).toEqual(["booking_key", "start_at", "token", "website"]);
  expect(forwarded.start_at).toBe(VALID.start_at);
  expect(forwarded.token).toBe(TOKEN);
});

test("passes the CRM's refusal straight through", async () => {
  stubForward(410, { detail: "This booking link has been withdrawn." });
  const response = await POST(postRequest(VALID, "203.0.113.31"), context());
  expect(response.status).toBe(410);
  await expect(response.json()).resolves.toMatchObject({
    detail: "This booking link has been withdrawn.",
  });
});

test("rejects a wrong content type and an oversized body before parsing", async () => {
  const fetchMock = stubForward(200, {});
  const wrongType = await POST(
    postRequest(VALID, "203.0.113.32", "text/plain"),
    context(),
  );
  expect(wrongType.status).toBe(415);

  const huge = new Request(`http://localhost:3000/api/public-booking/${TOKEN}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-forwarded-for": "203.0.113.33" },
    body: JSON.stringify({ ...VALID, booking_key: "x".repeat(8 * 1024) }),
  });
  expect((await POST(huge, context())).status).toBe(413);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("requires a time and a usable booking key", async () => {
  const fetchMock = stubForward(200, {});
  const noTime = await POST(postRequest({ booking_key: "long-enough-key" }, "203.0.113.34"), context());
  expect(noTime.status).toBe(422);
  const shortKey = await POST(
    postRequest({ start_at: VALID.start_at, booking_key: "short" }, "203.0.113.35"),
    context(),
  );
  expect(shortKey.status).toBe(422);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("throttles repeated attempts from one address", async () => {
  stubForward(200, { booking_reference: "APT-1" });
  const ip = "203.0.113.90";
  const statuses: number[] = [];
  for (let index = 0; index < 25; index += 1) {
    const response = await POST(postRequest(VALID, ip), context());
    statuses.push(response.status);
  }
  expect(statuses.filter((status) => status === 429).length).toBeGreaterThan(0);
});

test("never logs the token or the chosen time when forwarding fails", async () => {
  const fetchMock = vi.fn(() => Promise.reject(new Error("ECONNREFUSED")));
  vi.stubGlobal("fetch", fetchMock);
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const response = await POST(postRequest(VALID, "203.0.113.40"), context());
  expect(response.status).toBe(502);
  const logged = warn.mock.calls.flat().join(" ");
  expect(logged).not.toContain(TOKEN);
  expect(logged).not.toContain(VALID.start_at);
  warn.mockRestore();
});
