import { vi } from "vitest";
import type { SessionUser } from "@/lib/api";

export function makeUser(overrides: Partial<SessionUser> = {}): SessionUser {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    email: "owner@example.com",
    role: "owner",
    is_active: true,
    must_change_password: false,
    last_login_at: null,
    created_at: "2026-08-08T00:00:00Z",
    ...overrides,
  };
}

export function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => (body === null ? Promise.reject(new Error("no body")) : Promise.resolve(body)),
  };
}

/** Stub fetch with per-path responses; the first matching substring wins. */
export function stubFetchRoutes(routes: Array<[string, { status: number; body: unknown }]>) {
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const url = String(args[0]);
    for (const [fragment, { status, body }] of routes) {
      if (url.includes(fragment)) {
        return Promise.resolve(jsonResponse(status, body));
      }
    }
    return Promise.resolve(jsonResponse(404, { detail: "Not found." }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
