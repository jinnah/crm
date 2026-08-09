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

export type Lead = {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  company: string;
  status: string;
  source: string;
  assigned_to: string | null;
  assignee_email: string | null;
  next_follow_up_at: string | null;
  last_contacted_at: string | null;
  needs_review: boolean;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  custom_values: Record<string, unknown>;
};

export type LeadList = {
  items: Lead[];
  total: number;
  page: number;
  page_size: number;
};

export type Activity = {
  id: string;
  type: string;
  channel: string | null;
  direction: string | null;
  content: string;
  created_by_email: string | null;
  provider: string | null;
  external_event_id: string | null;
  occurred_at: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
};

export type AttentionQueue = {
  overdue: Lead[];
  due_today: Lead[];
  unassigned: Lead[];
  needs_review: Lead[];
};

export type CustomField = {
  id: string;
  key: string;
  label: string;
  type: "text" | "number" | "date" | "boolean" | "select";
  options: string[] | null;
  required: boolean;
  is_active: boolean;
  display_order: number;
};

export type AssignableUser = {
  id: string;
  email: string;
  role: Role;
};

export const LEAD_STATUSES = ["new", "contacted", "qualified", "won", "lost"] as const;

export const LEAD_SOURCES = [
  "manual",
  "web_form",
  "phone_call",
  "sms",
  "whatsapp",
  "facebook",
  "email",
  "other",
] as const;

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
