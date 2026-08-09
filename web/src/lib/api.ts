const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "owner" | "manager" | "team_member";

export type SessionUser = {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
};

export type SessionData = {
  user: SessionUser;
  csrf_token: string;
};

export type ApiResult<T> = { ok: boolean; status: number; data: T | null };

type ApiOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  csrfToken?: string;
};

// All authentication state lives in an HttpOnly cookie managed by the API;
// nothing security-related is ever written to localStorage or sessionStorage.
export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {},
): Promise<ApiResult<T>> {
  const { method = "GET", body, csrfToken } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1${path}`, {
      method,
      headers,
      credentials: "include",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    return { ok: false, status: 0, data: null };
  }

  let data: T | null = null;
  try {
    data = (await response.json()) as T;
  } catch {
    // No body (e.g. 204).
  }
  return { ok: response.ok, status: response.status, data };
}

export function errorDetail(data: unknown, fallback: string): string {
  if (
    data !== null &&
    typeof data === "object" &&
    "detail" in data &&
    typeof (data as { detail: unknown }).detail === "string"
  ) {
    return (data as { detail: string }).detail;
  }
  return fallback;
}
