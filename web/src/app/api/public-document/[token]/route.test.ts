import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { GET, POST } from "./route";

const ORIGINAL_ENV = { ...process.env };
const TOKEN = "AbCdEf0123456789_-DOCUMENTtoken0";

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

test("reads the document through the fixed internal path, token in the body", async () => {
  const fetchMock = stubForward(200, { kind: "quote", number: "Q-2026-0001" });
  const response = await GET(new Request("http://localhost:3000/x"), context());
  expect(response.status).toBe(200);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://api:8000/api/v1/internal/documents/info");
  expect(String(url)).not.toContain(TOKEN);
  expect(new Headers(init?.headers).get("X-Internal-Key")).toBe("internal-test-key");
  expect(JSON.parse(String(init?.body))).toEqual({ token: TOKEN });
  expect(response.headers.get("Cache-Control")).toBe("no-store");
});

test("refuses malformed tokens without contacting the CRM", async () => {
  const fetchMock = stubForward(200, {});
  for (const bad of ["short", "has spaces here ok", "../../etc"]) {
    expect((await GET(new Request("http://localhost:3000/x"), context(bad))).status).toBe(404);
  }
  expect(fetchMock).not.toHaveBeenCalled();
});

test("responding forwards only the response fields", async () => {
  const fetchMock = stubForward(200, { status: "accepted" });
  const response = await POST(
    new Request(`http://localhost:3000/api/public-document/${TOKEN}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-forwarded-for": "203.0.113.60" },
      body: JSON.stringify({
        accept: true,
        typed_name: "Pat Customer",
        website: "",
        lead_id: "aaaa",
        version_id: "bbbb",
      }),
    }),
    context(),
  );
  expect(response.status).toBe(200);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://api:8000/api/v1/internal/documents/respond");
  const forwarded = JSON.parse(String(init?.body));
  expect(Object.keys(forwarded).sort()).toEqual(["accept", "token", "typed_name", "website"]);
});

test("an explicit typed name is required", async () => {
  const fetchMock = stubForward(200, {});
  const response = await POST(
    new Request(`http://localhost:3000/api/public-document/${TOKEN}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-forwarded-for": "203.0.113.61" },
      body: JSON.stringify({ accept: true, typed_name: "   " }),
    }),
    context(),
  );
  expect(response.status).toBe(422);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("never logs the capability when forwarding fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("ECONNREFUSED"))),
  );
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const response = await GET(new Request("http://localhost:3000/x"), context());
  expect(response.status).toBe(502);
  expect(warn.mock.calls.flat().join(" ")).not.toContain(TOKEN);
  warn.mockRestore();
});
