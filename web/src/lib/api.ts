const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "owner" | "manager" | "team_member";

export type SessionUser = {
  id: string;
  email: string;
  role: Role;
  display_name: string;
  notification_phone: string | null;
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
  idempotencyKey?: string;
};

// All authentication state lives in an HttpOnly cookie managed by the API;
// nothing security-related is ever written to localStorage or sessionStorage.
export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {},
): Promise<ApiResult<T>> {
  const { method = "GET", body, csrfToken, idempotencyKey } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

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

/** Absolute URL for a versioned API path — for responses that are not JSON. */
export function apiUrl(path: string): string {
  return `${API_URL}/api/v1${path}`;
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
  first_inbound_at: string | null;
  response_due_at: string | null;
  first_response_at: string | null;
  first_response_seconds: number | null;
  response_target_met: boolean | null;
  response_overdue: boolean;
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

export type AttentionAppointment = {
  id: string;
  lead_id: string;
  lead_name: string | null;
  subject: string;
  start_at: string;
  timezone: string;
  status: string;
  detail: string | null;
};

export type AttentionVoiceCall = {
  id: string;
  lead_id: string;
  lead_name: string | null;
  reason: string;
  summary: string;
  occurred_at: string | null;
};

export type AttentionQueue = {
  overdue: Lead[];
  due_today: Lead[];
  unassigned: Lead[];
  needs_review: Lead[];
  unresponded: Lead[];
  appointments_overdue: AttentionAppointment[];
  appointments_upcoming: AttentionAppointment[];
  appointment_messages_failed: AttentionAppointment[];
  appointment_messages_unknown: AttentionAppointment[];
  voice_calls: AttentionVoiceCall[];
};

export type UserList = {
  items: SessionUser[];
  total: number;
  page: number;
  page_size: number;
};

export type VoiceCallRecord = {
  id: string;
  call_sid: string;
  lead_id: string;
  appointment_id: string | null;
  caller_phone: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  call_status: string;
  disposition: string;
  caller_name: string;
  service_requested: string;
  service_address: string;
  preferred_callback_window: string;
  appointment_preference: string;
  summary: string;
  urgency: string;
  requires_human_follow_up: boolean;
  transfer_outcome: string;
  disclosure_version: string;
  consent_result: string;
  ack_state: string;
  alert_state: string;
  recording_sid: string | null;
  purged_at: string | null;
  created_at: string;
};

export type VoiceSettings = {
  voice_ack_enabled: boolean;
  voice_ack_template: string;
  voice_alert_enabled: boolean;
  voice_alert_template: string;
  voice_alert_recipients: "business" | "assigned" | "both";
  voice_default_staff_id: string | null;
  voice_transcript_retention_enabled: boolean;
  voice_transcript_retention_days: number;
};

export type OutboundMessage = {
  id: string;
  lead_id: string;
  purpose: "human_reply" | "auto_acknowledgment" | "staff_alert" | "appointment";
  to_phone: string;
  body: string;
  status: "pending" | "submitted" | "delivered" | "failed" | "unknown";
  provider_sid: string | null;
  error_message: string | null;
  created_by_email: string | null;
  created_at: string;
  submitted_at: string | null;
  delivered_at: string | null;
  failed_at: string | null;
};

export type CommunicationSettings = {
  business_name: string;
  form_title: string;
  form_intro: string;
  acknowledgment_enabled: boolean;
  acknowledgment_template: string;
  alert_enabled: boolean;
  alert_template: string;
  alert_destination_phone: string | null;
  response_target_minutes: number;
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
  display_name: string;
};

export type AppointmentStatus = "scheduled" | "completed" | "canceled" | "no_show";

export type Appointment = {
  id: string;
  lead_id: string;
  lead_name: string | null;
  assigned_to: string | null;
  assignee_email: string | null;
  /** Display name for calendars; the email never enters narrow UI blocks. */
  assignee_name: string | null;
  subject: string;
  notes: string;
  start_at: string;
  end_at: string;
  timezone: string;
  status: AppointmentStatus;
  origin: "staff" | "customer" | "voice";
  /** Monotonic schedule revision; every mutation echoes the one it saw. */
  revision: number;
  booking_reference: string | null;
  cancellation_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type AppointmentNotification = {
  id: string;
  appointment_id: string;
  type: "confirmation" | "reminder" | "rescheduled" | "canceled";
  occurrence: string;
  scheduled_at: string;
  state: "pending" | "claimed" | "sent" | "failed" | "unknown" | "suppressed";
  failure_message: string | null;
};

export type AvailabilityDay = {
  date: string;
  timezone: string;
  duration_minutes: number;
  slots: string[];
};

export type BookingLink = {
  id: string;
  lead_id: string;
  assigned_to: string | null;
  expires_at: string;
  revoked_at: string | null;
  duration_minutes: number | null;
  created_at: string;
  last_used_at: string | null;
  /** Present only in the response that creates the link — never stored. */
  url: string | null;
};

/** The subset every scheduler needs; readable by any authenticated user. */
export type SchedulingBasics = {
  business_timezone: string;
  appointment_duration_minutes: number;
  min_booking_notice_minutes: number;
  max_booking_days_ahead: number;
  self_booking_enabled: boolean;
  business_hours: Record<string, string[][]> | null;
};

export type SchedulingSettings = {
  business_timezone: string;
  appointment_duration_minutes: number;
  min_booking_notice_minutes: number;
  max_booking_days_ahead: number;
  buffer_before_minutes: number;
  buffer_after_minutes: number;
  self_booking_enabled: boolean;
  appointment_confirmation_enabled: boolean;
  appointment_reminder_enabled: boolean;
  reminder_offset_minutes: number;
  second_reminder_offset_minutes: number | null;
  upcoming_window_hours: number;
  confirmation_template: string;
  reminder_template: string;
  appointment_canceled_template: string;
  appointment_rescheduled_template: string;
  business_hours: Record<string, string[][]> | null;
};

export const WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

export const WEEKDAY_LABELS: Record<string, string> = {
  mon: "Monday",
  tue: "Tuesday",
  wed: "Wednesday",
  thu: "Thursday",
  fri: "Friday",
  sat: "Saturday",
  sun: "Sunday",
};

export const APPOINTMENT_STATUS_LABELS: Record<string, string> = {
  scheduled: "Scheduled",
  completed: "Completed",
  canceled: "Canceled",
  no_show: "No-show",
};

export const LEAD_STATUSES = ["new", "contacted", "qualified", "won", "lost"] as const;

export const LEAD_SOURCES = [
  "manual",
  "web_form",
  "phone_call",
  "voice_call",
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
