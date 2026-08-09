import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { POST } from "./route";

const ORIGINAL_ENV = { ...process.env };

beforeEach(() => {
  process.env.N8N_FORM_WEBHOOK_URL = "http://n8n:5678/webhook/web-form";
  process.env.FORM_SHARED_SECRET = "test-form-secret";
});

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  vi.unstubAllGlobals();
});

function stubForward(status: number, body: unknown) {
  // Typed like fetch so mock.calls carries the [url, init] tuple types.
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

function jsonRequest(payload: unknown, ip = "203.0.113.10", contentType = "application/json") {
  return new Request("http://localhost:3000/api/public-request", {
    method: "POST",
    headers: { "Content-Type": contentType, "x-forwarded-for": ip },
    body: JSON.stringify(payload),
  });
}

const VALID = {
  submission_id: "sub-route-1",
  name: "Pat",
  email: "pat@example.com",
  message: "My roof leaks",
};

test("forwards the submission with the server-side shared secret", async () => {
  const fetchMock = stubForward(200, { status: "ok", replayed: false });
  const response = await POST(jsonRequest(VALID));
  expect(response.status).toBe(200);
  await expect(response.json()).resolves.toMatchObject({
    status: "ok",
    submission_id: "sub-route-1",
    duplicate: false,
  });

  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://n8n:5678/webhook/web-form");
  expect((init?.headers as Record<string, string>)["X-Form-Secret"]).toBe("test-form-secret");
  const forwarded = JSON.parse(String(init?.body));
  expect(forwarded.submission_id).toBe("sub-route-1");
  expect(forwarded.form).toBe("public-request");
  expect(forwarded.submitted_at).toBeTruthy();
});

test("reports a replayed submission as a duplicate", async () => {
  stubForward(200, { status: "ok", replayed: true });
  const response = await POST(jsonRequest(VALID, "203.0.113.11"));
  await expect(response.json()).resolves.toMatchObject({ duplicate: true });
});

test("generates a submission id when the client omits one", async () => {
  const fetchMock = stubForward(200, { status: "ok" });
  const withoutId = { name: VALID.name, email: VALID.email, message: VALID.message };
  await POST(jsonRequest(withoutId, "203.0.113.12"));
  const forwarded = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
  expect(forwarded.submission_id).toMatch(/[0-9a-f-]{16,}/);
});

test("rejects a wrong content type", async () => {
  const fetchMock = stubForward(200, {});
  const response = await POST(jsonRequest(VALID, "203.0.113.13", "text/plain"));
  expect(response.status).toBe(415);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("requires a message and a contact method", async () => {
  const fetchMock = stubForward(200, {});
  const response = await POST(
    jsonRequest({ submission_id: "sub-x", message: "no contact" }, "203.0.113.14"),
  );
  expect(response.status).toBe(422);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("rejects an oversized body before parsing it", async () => {
  const fetchMock = stubForward(200, {});
  const huge = "x".repeat(20 * 1024);
  const request = new Request("http://localhost:3000/api/public-request", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-forwarded-for": "203.0.113.15" },
    body: JSON.stringify({ ...VALID, message: huge }),
  });
  const response = await POST(request);
  expect(response.status).toBe(413);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("throttles repeated submissions from one address", async () => {
  stubForward(200, { status: "ok" });
  const ip = "203.0.113.99";
  const statuses: number[] = [];
  for (let index = 0; index < 7; index += 1) {
    const response = await POST(
      jsonRequest({ ...VALID, submission_id: `sub-throttle-${index}` }, ip),
    );
    statuses.push(response.status);
  }
  expect(statuses.filter((status) => status === 429).length).toBeGreaterThan(0);
});

test("never echoes the submission back when forwarding fails", async () => {
  stubForward(500, { error: "boom" });
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const response = await POST(
    jsonRequest({ ...VALID, submission_id: "sub-fail-1", message: "secret details" }, "203.0.113.20"),
  );
  expect(response.status).toBe(502);
  const logged = warn.mock.calls.flat().join(" ");
  expect(logged).not.toContain("secret details");
  expect(logged).not.toContain("pat@example.com");
  warn.mockRestore();
});
