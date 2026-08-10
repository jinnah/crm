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

// --- Jobs, documents, commercial records and document email --------------

export const JOB_STATUSES = [
  "new",
  "quoted",
  "approved",
  "scheduled",
  "in_progress",
  "completed",
  "canceled",
] as const;

export type JobStatus = (typeof JOB_STATUSES)[number];

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  new: "New",
  quoted: "Quoted",
  approved: "Approved",
  scheduled: "Scheduled",
  in_progress: "In progress",
  completed: "Completed",
  canceled: "Canceled",
};

export type Job = {
  id: string;
  job_number: string;
  lead_id: string;
  lead_name: string | null;
  title: string;
  service_type: string;
  service_address: string;
  status: JobStatus;
  assigned_to: string | null;
  assignee_name: string | null;
  scheduled_for: string | null;
  started_at: string | null;
  completed_at: string | null;
  internal_notes: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type JobList = {
  items: Job[];
  total: number;
  page: number;
  page_size: number;
};

export const DOCUMENT_CATEGORIES = [
  "receipt",
  "quote",
  "invoice",
  "contract",
  "permit",
  "warranty",
  "photo",
  "other",
] as const;

export type JobDocument = {
  id: string;
  job_id: string;
  title: string;
  category: string;
  description: string;
  original_filename: string;
  content_type: string;
  byte_size: number;
  sha256: string;
  scan_state: "pending" | "clean" | "infected" | "failed";
  scan_detail: string | null;
  has_preview: boolean;
  archived_at: string | null;
  deleted_at: string | null;
  created_at: string;
};

export type LineItem = {
  position?: number;
  description: string;
  quantity_milli: number;
  unit: string;
  unit_price_minor: number;
  discount_bp: number;
  tax_rate_bp: number;
  line_total_minor?: number;
};

export type CommercialDocument = {
  id: string;
  kind: "quote" | "invoice" | "receipt";
  job_id: string;
  status: string;
  number: string | null;
  currency: string;
  discount_bp: number;
  subtotal_minor: number;
  discount_total_minor: number;
  tax_total_minor: number;
  total_minor: number;
  amount_paid_minor: number;
  customer_notes: string;
  terms: string;
  valid_until: string | null;
  issued_at: string | null;
  due_at: string | null;
  current_version: number;
  responded_at: string | null;
  response_name: string | null;
  source_quote_id: string | null;
  converted_invoice_id: string | null;
  payment_id: string | null;
  voided_at: string | null;
  void_reason: string | null;
  created_at: string;
  lines: LineItem[];
};

export type Payment = {
  id: string;
  invoice_id: string;
  amount_minor: number;
  currency: string;
  method: string;
  paid_on: string;
  reference: string;
  internal_note: string;
  receipt_document_id: string | null;
  voided_at: string | null;
  void_reason: string | null;
  created_at: string;
};

export type EmailDeliveryRecord = {
  id: string;
  job_id: string;
  purpose: string;
  version_id: string | null;
  recipient: string;
  from_name: string;
  from_address: string;
  reply_to: string;
  subject: string;
  attach_pdf: boolean;
  status: string;
  attempts: number;
  provider_message_id: string | null;
  failure_class: string | null;
  failure_message: string | null;
  created_at: string;
  submitted_at: string | null;
  delivered_at: string | null;
};

export type DocumentSettings = {
  default_currency: string;
  quote_number_prefix: string;
  invoice_number_prefix: string;
  receipt_number_prefix: string;
  default_quote_valid_days: number;
  default_invoice_due_days: number;
  default_tax_rate_bp: number;
  business_email: string;
  business_phone: string;
  business_address: string;
  business_registration_id: string;
  email_from_display_name: string;
  email_reply_to: string;
  quote_email_subject: string;
  quote_email_body: string;
  invoice_email_subject: string;
  invoice_email_body: string;
  receipt_email_subject: string;
  receipt_email_body: string;
  secure_link_expiry_days: number;
  email_attach_pdf_default: boolean;
  effective_from_address: string;
  sender_configured: boolean;
};

export type DocumentConfigHealth = {
  storage: Record<string, string>;
  scanner: Record<string, string>;
  sender_configured: boolean;
};

/** Money helper: integer minor units → "199.99 USD". Display only — the
 * server owns every authoritative calculation. */
export function formatMinor(amountMinor: number, currency: string): string {
  const sign = amountMinor < 0 ? "-" : "";
  const value = Math.abs(amountMinor);
  const major = Math.floor(value / 100);
  const minor = value % 100;
  return `${sign}${major.toLocaleString()}.${String(minor).padStart(2, "0")} ${currency}`;
}

export function formatQuantity(quantityMilli: number): string {
  return String(quantityMilli / 1000);
}

/** UI mirror of the server's central lifecycle map — display only; the
 * server enforces every transition. */
export const JOB_TRANSITIONS: Record<JobStatus, JobStatus[]> = {
  new: ["quoted", "approved", "scheduled", "in_progress", "canceled"],
  quoted: ["approved", "scheduled", "in_progress", "canceled"],
  approved: ["scheduled", "in_progress", "completed", "canceled"],
  scheduled: ["in_progress", "completed", "canceled"],
  in_progress: ["completed", "canceled"],
  completed: [],
  canceled: [],
};
