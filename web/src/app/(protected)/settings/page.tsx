"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-context";
import { notifyBrandingChanged } from "@/components/brand-mark";
import { BrandingSection } from "@/components/settings/branding-section";
import {
  Button,
  Card,
  InlineError,
  InlineSuccess,
  PageHeader,
  SectionNav,
} from "@/components/ui";
import {
  api,
  errorDetail,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS,
  type AssignableUser,
  type CommunicationSettings,
  type DocumentConfigHealth,
  type DocumentSettings,
  type SchedulingSettings,
  type VoiceSettings,
} from "@/lib/api";

/* ----------------------------------------------------------------------- */
/* Section definitions                                                      */
/* ----------------------------------------------------------------------- */

type SectionKey =
  | "business"
  | "intake"
  | "messages"
  | "response"
  | "scheduling"
  | "availability"
  | "notifications"
  | "voice"
  | "documents";

const SECTIONS: ReadonlyArray<{ key: SectionKey; label: string }> = [
  { key: "business", label: "Business & branding" },
  { key: "intake", label: "Lead intake" },
  { key: "messages", label: "Automated messages" },
  { key: "response", label: "Response targets" },
  { key: "scheduling", label: "Scheduling rules" },
  { key: "availability", label: "Availability" },
  { key: "notifications", label: "Appointment notifications" },
  { key: "voice", label: "Voice calls" },
  { key: "documents", label: "Documents & email" },
];

/** Which fields each section owns, for dirty tracking and saving. */
const COMM_FIELDS: Partial<Record<SectionKey, Array<keyof CommunicationSettings>>> = {
  business: ["business_name"],
  intake: ["form_title", "form_intro"],
  messages: [
    "acknowledgment_enabled",
    "acknowledgment_template",
    "alert_enabled",
    "alert_template",
    "alert_destination_phone",
  ],
  response: ["response_target_minutes"],
};

const SCHED_FIELDS: Partial<Record<SectionKey, Array<keyof SchedulingSettings>>> = {
  scheduling: [
    "business_timezone",
    "appointment_duration_minutes",
    "min_booking_notice_minutes",
    "max_booking_days_ahead",
    "buffer_before_minutes",
    "buffer_after_minutes",
    "self_booking_enabled",
  ],
  availability: ["business_hours"],
  notifications: [
    "appointment_confirmation_enabled",
    "appointment_reminder_enabled",
    "reminder_offset_minutes",
    "second_reminder_offset_minutes",
    "upcoming_window_hours",
    "confirmation_template",
    "reminder_template",
    "appointment_canceled_template",
    "appointment_rescheduled_template",
  ],
};

const VOICE_FIELDS: Array<keyof VoiceSettings> = [
  "voice_ack_enabled",
  "voice_ack_template",
  "voice_alert_enabled",
  "voice_alert_template",
  "voice_alert_recipients",
  "voice_default_staff_id",
  "voice_transcript_retention_enabled",
  "voice_transcript_retention_days",
];

const DOC_FIELDS: Array<keyof DocumentSettings> = [
  "default_currency",
  "quote_number_prefix",
  "invoice_number_prefix",
  "receipt_number_prefix",
  "default_quote_valid_days",
  "default_invoice_due_days",
  "default_tax_rate_bp",
  "business_email",
  "business_phone",
  "business_address",
  "business_registration_id",
  "email_from_display_name",
  "email_reply_to",
  "quote_email_subject",
  "quote_email_body",
  "invoice_email_subject",
  "invoice_email_body",
  "receipt_email_subject",
  "receipt_email_body",
  "secure_link_expiry_days",
  "email_attach_pdf_default",
];

const EMAIL_VARIABLES = [
  "customer_name",
  "business_name",
  "job_number",
  "document_type",
  "document_number",
  "document_total",
  "due_date",
  "secure_document_link",
  "reply_to",
];

const MESSAGE_VARIABLES = ["lead_name", "business_name", "source", "lead_id"];
const VOICE_VARIABLES = [
  "lead_name",
  "business_name",
  "service_requested",
  "call_summary",
  "callback_window",
  "assigned_staff",
  "lead_id",
];
const APPOINTMENT_VARIABLES = [
  "lead_name",
  "business_name",
  "appointment_date",
  "appointment_time",
  "assigned_staff",
  "appointment_subject",
  "booking_reference",
];
const SMS_LIMIT = 1600;

/* ----------------------------------------------------------------------- */
/* Page                                                                     */
/* ----------------------------------------------------------------------- */

export default function SettingsPage() {
  const { user, csrfToken } = useAuth();
  const isOwner = user.role === "owner";

  const [savedComm, setSavedComm] = useState<CommunicationSettings | null>(null);
  const [savedSched, setSavedSched] = useState<SchedulingSettings | null>(null);
  const [savedVoice, setSavedVoice] = useState<VoiceSettings | null>(null);
  const [savedDocs, setSavedDocs] = useState<DocumentSettings | null>(null);
  const [comm, setComm] = useState<CommunicationSettings | null>(null);
  const [sched, setSched] = useState<SchedulingSettings | null>(null);
  const [voice, setVoice] = useState<VoiceSettings | null>(null);
  const [docs, setDocs] = useState<DocumentSettings | null>(null);
  const [configHealth, setConfigHealth] = useState<DocumentConfigHealth | null>(null);
  const [staff, setStaff] = useState<AssignableUser[]>([]);
  const [section, setSection] = useState<SectionKey>("business");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOwner) return;
    let cancelled = false;
    void api<CommunicationSettings>("/settings/communication").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load settings."));
        return;
      }
      setSavedComm(result.data);
      setComm(result.data);
    });
    void api<SchedulingSettings>("/settings/scheduling").then((result) => {
      if (cancelled) return;
      if (result.ok && result.data !== null) {
        setSavedSched(result.data);
        setSched(result.data);
      }
    });
    void api<VoiceSettings>("/settings/voice").then((result) => {
      if (cancelled) return;
      if (result.ok && result.data !== null) {
        setSavedVoice(result.data);
        setVoice(result.data);
      }
    });
    void api<AssignableUser[]>("/leads/assignable-users").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setStaff(result.data);
    });
    void api<DocumentSettings>("/settings/documents").then((result) => {
      if (cancelled) return;
      if (result.ok && result.data !== null) {
        setSavedDocs(result.data);
        setDocs(result.data);
      }
    });
    void api<DocumentConfigHealth>("/settings/documents/health").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setConfigHealth(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [isOwner]);

  // A saved confirmation should register, then get out of the way.
  useEffect(() => {
    if (notice === null) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const dirtySections = useMemo(() => {
    const dirty = new Set<SectionKey>();
    for (const { key } of SECTIONS) {
      const commFields = COMM_FIELDS[key];
      if (commFields && savedComm !== null && comm !== null) {
        if (commFields.some((field) => !same(comm[field], savedComm[field]))) dirty.add(key);
      }
      const schedFields = SCHED_FIELDS[key];
      if (schedFields && savedSched !== null && sched !== null) {
        if (schedFields.some((field) => !same(sched[field], savedSched[field]))) dirty.add(key);
      }
    }
    if (
      savedVoice !== null &&
      voice !== null &&
      VOICE_FIELDS.some((field) => !same(voice[field], savedVoice[field]))
    ) {
      dirty.add("voice");
    }
    if (
      savedDocs !== null &&
      docs !== null &&
      DOC_FIELDS.some((field) => !same(docs[field], savedDocs[field]))
    ) {
      dirty.add("documents");
    }
    return dirty;
  }, [comm, savedComm, sched, savedSched, voice, savedVoice, docs, savedDocs]);

  // Leaving the page with unsaved edits gets a browser warning.
  useEffect(() => {
    if (dirtySections.size === 0) return;
    function onBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirtySections]);

  if (!isOwner) {
    return (
      <section>
        <PageHeader title="Settings" />
        <InlineError>You do not have access to settings.</InlineError>
      </section>
    );
  }

  if (comm === null || savedComm === null) {
    return (
      <p className="page-status" role="status">
        {error ?? "Loading settings…"}
      </p>
    );
  }

  function switchSection(next: SectionKey) {
    if (
      dirtySections.has(section) &&
      !window.confirm("This section has unsaved changes. Leave without saving?")
    ) {
      return;
    }
    // Discard the abandoned edits so the dirty flag does not follow the user.
    if (dirtySections.has(section)) {
      setComm(savedComm);
      setSched(savedSched);
      setVoice(savedVoice);
      setDocs(savedDocs);
    }
    setError(null);
    setNotice(null);
    setSection(next);
  }

  async function saveSection(key: SectionKey, sectionLabel: string) {
    setError(null);
    setNotice(null);
    setSaving(true);
    try {
      const commFields = COMM_FIELDS[key];
      if (commFields && comm !== null) {
        const body = Object.fromEntries(commFields.map((field) => [field, comm[field]]));
        const result = await api<CommunicationSettings>("/settings/communication", {
          method: "PATCH",
          csrfToken,
          body,
        });
        if (!result.ok || result.data === null) {
          setError(errorDetail(result.data, "Unable to save these settings."));
          return;
        }
        setSavedComm(result.data);
        setComm(result.data);
        // The shell shows the business name; let it refresh immediately.
        if (key === "business") notifyBrandingChanged();
      }
      const schedFields = SCHED_FIELDS[key];
      if (schedFields && sched !== null) {
        const body = Object.fromEntries(schedFields.map((field) => [field, sched[field]]));
        const result = await api<SchedulingSettings>("/settings/scheduling", {
          method: "PATCH",
          csrfToken,
          body,
        });
        if (!result.ok || result.data === null) {
          setError(errorDetail(result.data, "Unable to save these settings."));
          return;
        }
        setSavedSched(result.data);
        setSched(result.data);
      }
      if (key === "voice" && voice !== null) {
        const body: Record<string, unknown> = Object.fromEntries(
          VOICE_FIELDS.map((field) => [field, voice[field]]),
        );
        if (voice.voice_default_staff_id === null) {
          // Clearing is an explicit request, never an omitted field.
          delete body.voice_default_staff_id;
          body.clear_default_staff = true;
        }
        const result = await api<VoiceSettings>("/settings/voice", {
          method: "PATCH",
          csrfToken,
          body,
        });
        if (!result.ok || result.data === null) {
          setError(errorDetail(result.data, "Unable to save these settings."));
          return;
        }
        setSavedVoice(result.data);
        setVoice(result.data);
      }
      if (key === "documents" && docs !== null) {
        const body = Object.fromEntries(DOC_FIELDS.map((field) => [field, docs[field]]));
        const result = await api<DocumentSettings>("/settings/documents", {
          method: "PATCH",
          csrfToken,
          body,
        });
        if (!result.ok || result.data === null) {
          setError(errorDetail(result.data, "Unable to save these settings."));
          return;
        }
        setSavedDocs(result.data);
        setDocs(result.data);
      }
      setNotice(`${sectionLabel} saved.`);
    } finally {
      setSaving(false);
    }
  }

  const updateComm = <K extends keyof CommunicationSettings>(
    key: K,
    value: CommunicationSettings[K],
  ) => setComm((current) => (current === null ? current : { ...current, [key]: value }));

  const updateSched = <K extends keyof SchedulingSettings>(
    key: K,
    value: SchedulingSettings[K],
  ) => setSched((current) => (current === null ? current : { ...current, [key]: value }));

  const updateVoice = <K extends keyof VoiceSettings>(key: K, value: VoiceSettings[K]) =>
    setVoice((current) => (current === null ? current : { ...current, [key]: value }));

  const updateDocs = <K extends keyof DocumentSettings>(key: K, value: DocumentSettings[K]) =>
    setDocs((current) => (current === null ? current : { ...current, [key]: value }));

  const schedReady = sched !== null && savedSched !== null;
  const voiceReady = voice !== null && savedVoice !== null;
  const docsReady = docs !== null && savedDocs !== null;
  const currentLabel = SECTIONS.find((entry) => entry.key === section)?.label ?? "Settings";

  return (
    <section>
      <PageHeader
        title="Settings"
        description="How your business presents itself, captures leads and runs its schedule."
      />

      <div className="settings-layout">
        <SectionNav
          sections={SECTIONS}
          active={section}
          onSelect={switchSection}
          label="Settings sections"
        />

        <div className="settings-content">
          {error !== null && <InlineError>{error}</InlineError>}
          {notice !== null && <InlineSuccess>{notice}</InlineSuccess>}

          <div className="stack narrow-form">
        {section === "business" && (
          <div className="settings-two-col">
            <Card
              title="Business profile"
              description="The name customers see in messages and on booking pages."
            >
              <div className="form-field">
                <label htmlFor="business-name">Business display name</label>
                <input
                  id="business-name"
                  value={comm.business_name}
                  onChange={(event) => updateComm("business_name", event.target.value)}
                />
              </div>
              <SaveBar
                dirty={dirtySections.has("business")}
                saving={saving}
                label="Save business profile"
                onSave={() => void saveSection("business", currentLabel)}
              />
            </Card>
            <BrandingSection csrfToken={csrfToken} />
          </div>
        )}

        {section === "intake" && (
          <Card
            title="Public request form"
            description="The embedded form at /request that captures new leads."
          >
            <div className="form-field">
              <label htmlFor="form-title">Form title</label>
              <input
                id="form-title"
                value={comm.form_title}
                onChange={(event) => updateComm("form_title", event.target.value)}
              />
            </div>
            <div className="form-field">
              <label htmlFor="form-intro">Form introduction</label>
              <textarea
                id="form-intro"
                rows={3}
                value={comm.form_intro}
                onChange={(event) => updateComm("form_intro", event.target.value)}
              />
              <p className="form-help">Shown above the form and on the booking page.</p>
            </div>
            <SaveBar
              dirty={dirtySections.has("intake")}
              saving={saving}
              label="Save lead intake"
              onSave={() => void saveSection("intake", currentLabel)}
            />
          </Card>
        )}

        {section === "messages" && (
          <Card
            title="Automated messages"
            description="Sent after a new request is stored — never before."
          >
            <div className="form-field form-field-checkbox">
              <label htmlFor="ack-enabled">
                <input
                  id="ack-enabled"
                  type="checkbox"
                  checked={comm.acknowledgment_enabled}
                  onChange={(event) => updateComm("acknowledgment_enabled", event.target.checked)}
                />{" "}
                Send an automatic acknowledgment to new leads
              </label>
            </div>
            <TemplateField
              id="ack-template"
              label="Acknowledgment message"
              value={comm.acknowledgment_template}
              onChange={(value) => updateComm("acknowledgment_template", value)}
              variables={MESSAGE_VARIABLES}
              disabled={!comm.acknowledgment_enabled}
            />

            <div className="form-field form-field-checkbox">
              <label htmlFor="alert-enabled">
                <input
                  id="alert-enabled"
                  type="checkbox"
                  checked={comm.alert_enabled}
                  onChange={(event) => updateComm("alert_enabled", event.target.checked)}
                />{" "}
                Send a new-lead alert to the business
              </label>
            </div>
            <div className="form-field">
              <label htmlFor="alert-phone">Notification destination phone</label>
              <input
                id="alert-phone"
                type="tel"
                aria-describedby="alert-phone-help"
                value={comm.alert_destination_phone ?? ""}
                onChange={(event) => updateComm("alert_destination_phone", event.target.value)}
              />
              <p id="alert-phone-help" className="form-help">
                International format, for example +15555550123.
              </p>
            </div>
            <TemplateField
              id="alert-template"
              label="New-lead alert message"
              value={comm.alert_template}
              onChange={(value) => updateComm("alert_template", value)}
              variables={MESSAGE_VARIABLES}
              disabled={!comm.alert_enabled}
            />
            <SaveBar
              dirty={dirtySections.has("messages")}
              saving={saving}
              label="Save automated messages"
              onSave={() => void saveSection("messages", currentLabel)}
            />
          </Card>
        )}

        {section === "response" && (
          <Card
            title="Response target"
            description="New requests that wait longer than this appear on Today as overdue."
          >
            <div className="form-field">
              <label htmlFor="response-target">First-response target (minutes)</label>
              <input
                id="response-target"
                type="number"
                min={1}
                value={comm.response_target_minutes}
                onChange={(event) =>
                  updateComm("response_target_minutes", Number(event.target.value) || 1)
                }
              />
            </div>
            <SaveBar
              dirty={dirtySections.has("response")}
              saving={saving}
              label="Save response target"
              onSave={() => void saveSection("response", currentLabel)}
            />
          </Card>
        )}

        {section === "scheduling" && schedReady && (
          <Card
            title="Scheduling rules"
            description="How appointments are offered, in your business time zone."
          >
            <div className="form-field">
              <label htmlFor="business-timezone">Business time zone</label>
              <input
                id="business-timezone"
                aria-describedby="timezone-help"
                value={sched.business_timezone}
                onChange={(event) => updateSched("business_timezone", event.target.value)}
              />
              <p id="timezone-help" className="form-help">
                An IANA name such as America/New_York. Appointment times are shown in this zone.
              </p>
            </div>
            <div className="form-field">
              <label htmlFor="default-duration">Default appointment length (minutes)</label>
              <input
                id="default-duration"
                type="number"
                min={5}
                max={720}
                value={sched.appointment_duration_minutes}
                onChange={(event) =>
                  updateSched("appointment_duration_minutes", Number(event.target.value) || 5)
                }
              />
            </div>
            <div className="form-field">
              <label htmlFor="min-notice">Minimum booking notice (minutes)</label>
              <input
                id="min-notice"
                type="number"
                min={0}
                value={sched.min_booking_notice_minutes}
                onChange={(event) =>
                  updateSched("min_booking_notice_minutes", Number(event.target.value) || 0)
                }
              />
            </div>
            <div className="form-field">
              <label htmlFor="max-ahead">Bookable how far ahead (days)</label>
              <input
                id="max-ahead"
                type="number"
                min={1}
                max={365}
                value={sched.max_booking_days_ahead}
                onChange={(event) =>
                  updateSched("max_booking_days_ahead", Number(event.target.value) || 1)
                }
              />
            </div>
            <div className="form-field">
              <label htmlFor="buffer-before">Buffer before an appointment (minutes)</label>
              <input
                id="buffer-before"
                type="number"
                min={0}
                max={240}
                value={sched.buffer_before_minutes}
                onChange={(event) =>
                  updateSched("buffer_before_minutes", Number(event.target.value) || 0)
                }
              />
            </div>
            <div className="form-field">
              <label htmlFor="buffer-after">Buffer after an appointment (minutes)</label>
              <input
                id="buffer-after"
                type="number"
                min={0}
                max={240}
                value={sched.buffer_after_minutes}
                onChange={(event) =>
                  updateSched("buffer_after_minutes", Number(event.target.value) || 0)
                }
              />
            </div>
            <div className="form-field form-field-checkbox">
              <label htmlFor="self-booking">
                <input
                  id="self-booking"
                  type="checkbox"
                  checked={sched.self_booking_enabled}
                  onChange={(event) => updateSched("self_booking_enabled", event.target.checked)}
                />{" "}
                Let customers book and change their own appointments with a link
              </label>
            </div>
            <SaveBar
              dirty={dirtySections.has("scheduling")}
              saving={saving}
              label="Save scheduling rules"
              onSave={() => void saveSection("scheduling", currentLabel)}
            />
          </Card>
        )}

        {section === "availability" && schedReady && (
          <Card
            title="Business hours"
            description="One opening window per day. Clear a day to close it."
          >
            <BusinessHoursEditor
              hours={sched.business_hours}
              onChange={(hours) => updateSched("business_hours", hours)}
            />
            <SaveBar
              dirty={dirtySections.has("availability")}
              saving={saving}
              label="Save availability"
              onSave={() => void saveSection("availability", currentLabel)}
            />
          </Card>
        )}

        {section === "notifications" && schedReady && (
          <Card
            title="Appointment notifications"
            description="Confirmations and reminders sent for booked appointments."
          >
            <div className="form-field form-field-checkbox">
              <label htmlFor="confirmation-enabled">
                <input
                  id="confirmation-enabled"
                  type="checkbox"
                  checked={sched.appointment_confirmation_enabled}
                  onChange={(event) =>
                    updateSched("appointment_confirmation_enabled", event.target.checked)
                  }
                />{" "}
                Send a confirmation when an appointment is booked or canceled
              </label>
            </div>
            <TemplateField
              id="confirmation-template"
              label="Confirmation message"
              value={sched.confirmation_template}
              onChange={(value) => updateSched("confirmation_template", value)}
              variables={APPOINTMENT_VARIABLES}
              disabled={!sched.appointment_confirmation_enabled}
            />
            <TemplateField
              id="canceled-template"
              label="Cancellation message"
              value={sched.appointment_canceled_template}
              onChange={(value) => updateSched("appointment_canceled_template", value)}
              variables={APPOINTMENT_VARIABLES}
              disabled={!sched.appointment_confirmation_enabled}
            />

            <div className="form-field form-field-checkbox">
              <label htmlFor="reminder-enabled">
                <input
                  id="reminder-enabled"
                  type="checkbox"
                  checked={sched.appointment_reminder_enabled}
                  onChange={(event) =>
                    updateSched("appointment_reminder_enabled", event.target.checked)
                  }
                />{" "}
                Send reminders before an appointment
              </label>
            </div>
            <div className="form-field">
              <label htmlFor="reminder-offset">First reminder, minutes before</label>
              <input
                id="reminder-offset"
                type="number"
                min={5}
                disabled={!sched.appointment_reminder_enabled}
                value={sched.reminder_offset_minutes}
                onChange={(event) =>
                  updateSched("reminder_offset_minutes", Number(event.target.value) || 5)
                }
              />
            </div>
            <div className="form-field">
              <label htmlFor="second-reminder-offset">
                Second reminder, minutes before (optional)
              </label>
              <input
                id="second-reminder-offset"
                type="number"
                min={5}
                disabled={!sched.appointment_reminder_enabled}
                value={sched.second_reminder_offset_minutes ?? ""}
                onChange={(event) =>
                  updateSched(
                    "second_reminder_offset_minutes",
                    event.target.value === "" ? null : Number(event.target.value),
                  )
                }
              />
            </div>
            <TemplateField
              id="reminder-template"
              label="Reminder message"
              value={sched.reminder_template}
              onChange={(value) => updateSched("reminder_template", value)}
              variables={APPOINTMENT_VARIABLES}
              disabled={!sched.appointment_reminder_enabled}
            />
            <TemplateField
              id="rescheduled-template"
              label="Rescheduled message"
              value={sched.appointment_rescheduled_template}
              onChange={(value) => updateSched("appointment_rescheduled_template", value)}
              variables={APPOINTMENT_VARIABLES}
            />
            <div className="form-field">
              <label htmlFor="upcoming-window">
                Show appointments on Today this many hours ahead
              </label>
              <input
                id="upcoming-window"
                type="number"
                min={1}
                max={336}
                value={sched.upcoming_window_hours}
                onChange={(event) =>
                  updateSched("upcoming_window_hours", Number(event.target.value) || 1)
                }
              />
            </div>
            <SaveBar
              dirty={dirtySections.has("notifications")}
              saving={saving}
              label="Save appointment notifications"
              onSave={() => void saveSection("notifications", currentLabel)}
            />
          </Card>
        )}

        {section === "voice" && voiceReady && (
          <Card
            title="Voice calls"
            description="What happens after the AI phone agent captures a call. The agent itself is configured in n8n; these controls decide the CRM's messages and retention."
          >
            <div className="form-field form-field-checkbox">
              <label htmlFor="voice-ack-enabled">
                <input
                  id="voice-ack-enabled"
                  type="checkbox"
                  checked={voice.voice_ack_enabled}
                  onChange={(event) => updateVoice("voice_ack_enabled", event.target.checked)}
                />{" "}
                Text the caller an acknowledgment after a successfully captured call
              </label>
            </div>
            <TemplateField
              id="voice-ack-template"
              label="Caller acknowledgment message"
              value={voice.voice_ack_template}
              onChange={(value) => updateVoice("voice_ack_template", value)}
              variables={VOICE_VARIABLES}
              disabled={!voice.voice_ack_enabled}
            />

            <div className="form-field form-field-checkbox">
              <label htmlFor="voice-alert-enabled">
                <input
                  id="voice-alert-enabled"
                  type="checkbox"
                  checked={voice.voice_alert_enabled}
                  onChange={(event) => updateVoice("voice_alert_enabled", event.target.checked)}
                />{" "}
                Send a staff alert about the new call
              </label>
            </div>
            <div className="form-field">
              <label htmlFor="voice-alert-recipients">Alert goes to</label>
              <select
                id="voice-alert-recipients"
                disabled={!voice.voice_alert_enabled}
                value={voice.voice_alert_recipients}
                onChange={(event) =>
                  updateVoice(
                    "voice_alert_recipients",
                    event.target.value as VoiceSettings["voice_alert_recipients"],
                  )
                }
              >
                <option value="business">The business number</option>
                <option value="assigned">The assigned staff member</option>
                <option value="both">Both</option>
              </select>
              <p className="form-help">
                Staff alerts use each person&apos;s notification phone from the Users page. If the
                assigned person has none, the alert falls back to the business number and the call
                is flagged for attention.
              </p>
            </div>
            <TemplateField
              id="voice-alert-template"
              label="Staff alert message"
              value={voice.voice_alert_template}
              onChange={(value) => updateVoice("voice_alert_template", value)}
              variables={VOICE_VARIABLES}
              disabled={!voice.voice_alert_enabled}
            />

            <div className="form-field">
              <label htmlFor="voice-default-staff">Default staff member for phone bookings</label>
              <select
                id="voice-default-staff"
                value={voice.voice_default_staff_id ?? ""}
                onChange={(event) =>
                  updateVoice("voice_default_staff_id", event.target.value || null)
                }
              >
                <option value="">None — the agent records a preference instead of booking</option>
                {staff.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.display_name || option.email}
                  </option>
                ))}
              </select>
              <p className="form-help">
                Used when a caller&apos;s lead has no assigned staff member. Bookings follow the
                same rules as the public booking page.
              </p>
            </div>

            <div className="form-field form-field-checkbox">
              <label htmlFor="voice-retention-enabled">
                <input
                  id="voice-retention-enabled"
                  type="checkbox"
                  checked={voice.voice_transcript_retention_enabled}
                  onChange={(event) =>
                    updateVoice("voice_transcript_retention_enabled", event.target.checked)
                  }
                />{" "}
                Keep full call transcripts (off by default)
              </label>
            </div>
            <div className="form-field">
              <label htmlFor="voice-retention-days">Delete transcripts after (days)</label>
              <input
                id="voice-retention-days"
                type="number"
                min={1}
                max={365}
                disabled={!voice.voice_transcript_retention_enabled}
                aria-describedby="voice-retention-help"
                value={voice.voice_transcript_retention_days}
                onChange={(event) =>
                  updateVoice(
                    "voice_transcript_retention_days",
                    Math.min(365, Math.max(1, Number(event.target.value) || 1)),
                  )
                }
              />
              <p id="voice-retention-help" className="form-help">
                Transcripts are stored only for calls where the caller heard the disclosure and
                did not object. After the retention period the transcript and recording reference
                are purged; the call&apos;s summary, outcome and audit record remain.
              </p>
            </div>
            <SaveBar
              dirty={dirtySections.has("voice")}
              saving={saving}
              label="Save voice call settings"
              onSave={() => void saveSection("voice", currentLabel)}
            />
          </Card>
        )}

        {(section === "scheduling" || section === "availability" || section === "notifications") &&
          !schedReady && (
            <p className="page-status" role="status">
              Loading scheduling settings…
            </p>
          )}

        {section === "voice" && !voiceReady && (
          <p className="page-status" role="status">
            Loading voice settings…
          </p>
        )}

        {section === "documents" && docsReady && (
          <>
            <Card
              title="Business document details"
              description="What appears on generated quotes, invoices and receipts."
            >
              <div className="form-field">
                <label htmlFor="docs-currency">Default currency (ISO code)</label>
                <input
                  id="docs-currency"
                  maxLength={3}
                  style={{ maxWidth: "6rem", textTransform: "uppercase" }}
                  value={docs.default_currency}
                  onChange={(event) =>
                    updateDocs("default_currency", event.target.value.toUpperCase())
                  }
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-email">Business email</label>
                <input
                  id="docs-email"
                  maxLength={320}
                  value={docs.business_email}
                  onChange={(event) => updateDocs("business_email", event.target.value)}
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-phone">Business phone</label>
                <input
                  id="docs-phone"
                  maxLength={32}
                  value={docs.business_phone}
                  onChange={(event) => updateDocs("business_phone", event.target.value)}
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-address">Business address</label>
                <textarea
                  id="docs-address"
                  rows={2}
                  maxLength={500}
                  value={docs.business_address}
                  onChange={(event) => updateDocs("business_address", event.target.value)}
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-registration">
                  Registration / tax identifier (optional)
                </label>
                <input
                  id="docs-registration"
                  maxLength={100}
                  value={docs.business_registration_id}
                  onChange={(event) =>
                    updateDocs("business_registration_id", event.target.value)
                  }
                />
                <p className="form-help">
                  Printed on documents when set. The CRM does not calculate or certify
                  jurisdiction-specific tax obligations.
                </p>
              </div>
              <div className="form-field">
                <label htmlFor="docs-quote-prefix">Quote number prefix</label>
                <input
                  id="docs-quote-prefix"
                  maxLength={8}
                  style={{ maxWidth: "8rem" }}
                  value={docs.quote_number_prefix}
                  onChange={(event) => updateDocs("quote_number_prefix", event.target.value)}
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-invoice-prefix">Invoice number prefix</label>
                <input
                  id="docs-invoice-prefix"
                  maxLength={8}
                  style={{ maxWidth: "8rem" }}
                  value={docs.invoice_number_prefix}
                  onChange={(event) => updateDocs("invoice_number_prefix", event.target.value)}
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-receipt-prefix">Receipt number prefix</label>
                <input
                  id="docs-receipt-prefix"
                  maxLength={8}
                  style={{ maxWidth: "8rem" }}
                  value={docs.receipt_number_prefix}
                  onChange={(event) => updateDocs("receipt_number_prefix", event.target.value)}
                />
                <p className="form-help">
                  Prefixes apply to newly issued numbers only; issued numbers never change.
                </p>
              </div>
              <div className="form-field">
                <label htmlFor="docs-valid-days">Quote validity (days)</label>
                <input
                  id="docs-valid-days"
                  type="number"
                  min={1}
                  max={365}
                  style={{ maxWidth: "8rem" }}
                  value={docs.default_quote_valid_days}
                  onChange={(event) =>
                    updateDocs("default_quote_valid_days", Number(event.target.value) || 1)
                  }
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-due-days">Invoice due period (days)</label>
                <input
                  id="docs-due-days"
                  type="number"
                  min={1}
                  max={365}
                  style={{ maxWidth: "8rem" }}
                  value={docs.default_invoice_due_days}
                  onChange={(event) =>
                    updateDocs("default_invoice_due_days", Number(event.target.value) || 1)
                  }
                />
              </div>
              <SaveBar
                dirty={dirtySections.has("documents")}
                saving={saving}
                label="Save document settings"
                onSave={() => void saveSection("documents", currentLabel)}
              />
            </Card>

            <Card
              title="Document email"
              description="How quotes, invoices and receipts travel to customers."
            >
              <div className="form-field">
                <label htmlFor="docs-from-address">Verified sender address</label>
                <input
                  id="docs-from-address"
                  value={docs.effective_from_address || "Not configured"}
                  readOnly
                  aria-describedby="docs-from-help"
                />
                <p id="docs-from-help" className="form-help">
                  Deployment configuration (DOCUMENT_EMAIL_FROM_ADDRESS), shown read-only.
                  {docs.sender_configured
                    ? " Sending is enabled."
                    : " Sending is disabled until an address is configured and verified — drafts and PDFs still work."}
                </p>
              </div>
              <div className="form-field">
                <label htmlFor="docs-from-name">From display name</label>
                <input
                  id="docs-from-name"
                  maxLength={200}
                  placeholder={comm.business_name}
                  value={docs.email_from_display_name}
                  onChange={(event) =>
                    updateDocs("email_from_display_name", event.target.value)
                  }
                />
              </div>
              <div className="form-field">
                <label htmlFor="docs-reply-to">Reply-To address</label>
                <input
                  id="docs-reply-to"
                  maxLength={320}
                  value={docs.email_reply_to}
                  onChange={(event) => updateDocs("email_reply_to", event.target.value)}
                />
                <p className="form-help">The monitored address customer replies land in.</p>
              </div>
              <div className="form-field form-field-checkbox">
                <label htmlFor="docs-attach-default">
                  <input
                    id="docs-attach-default"
                    type="checkbox"
                    checked={docs.email_attach_pdf_default}
                    onChange={(event) =>
                      updateDocs("email_attach_pdf_default", event.target.checked)
                    }
                  />{" "}
                  Attach the PDF by default (a secure link is always included)
                </label>
              </div>
              <div className="form-field">
                <label htmlFor="docs-link-expiry">Secure link expiry (days)</label>
                <input
                  id="docs-link-expiry"
                  type="number"
                  min={1}
                  max={365}
                  style={{ maxWidth: "8rem" }}
                  value={docs.secure_link_expiry_days}
                  onChange={(event) =>
                    updateDocs("secure_link_expiry_days", Number(event.target.value) || 1)
                  }
                />
              </div>
              <TemplateField
                id="docs-quote-subject"
                label="Quote email subject"
                value={docs.quote_email_subject}
                onChange={(value) => updateDocs("quote_email_subject", value)}
                variables={EMAIL_VARIABLES}
              />
              <TemplateField
                id="docs-quote-body"
                label="Quote email body"
                value={docs.quote_email_body}
                onChange={(value) => updateDocs("quote_email_body", value)}
                variables={EMAIL_VARIABLES}
              />
              <TemplateField
                id="docs-invoice-subject"
                label="Invoice email subject"
                value={docs.invoice_email_subject}
                onChange={(value) => updateDocs("invoice_email_subject", value)}
                variables={EMAIL_VARIABLES}
              />
              <TemplateField
                id="docs-invoice-body"
                label="Invoice email body"
                value={docs.invoice_email_body}
                onChange={(value) => updateDocs("invoice_email_body", value)}
                variables={EMAIL_VARIABLES}
              />
              <TemplateField
                id="docs-receipt-subject"
                label="Receipt email subject"
                value={docs.receipt_email_subject}
                onChange={(value) => updateDocs("receipt_email_subject", value)}
                variables={EMAIL_VARIABLES}
              />
              <TemplateField
                id="docs-receipt-body"
                label="Receipt email body"
                value={docs.receipt_email_body}
                onChange={(value) => updateDocs("receipt_email_body", value)}
                variables={EMAIL_VARIABLES}
              />
              <SaveBar
                dirty={dirtySections.has("documents")}
                saving={saving}
                label="Save document email settings"
                onSave={() => void saveSection("documents", currentLabel)}
              />
            </Card>

            {configHealth !== null && (
              <Card
                title="Configuration health"
                description="Storage, scanning and sender state — no secrets are shown."
              >
                <ul className="document-list">
                  <li className="document-row">
                    <span className="document-title">
                      <span style={{ fontWeight: 600 }}>Document storage</span>
                      <span className="cell-secondary">
                        {configHealth.storage.backend} — {configHealth.storage.status}
                      </span>
                    </span>
                  </li>
                  <li className="document-row">
                    <span className="document-title">
                      <span style={{ fontWeight: 600 }}>Malware scanner</span>
                      <span className="cell-secondary">
                        {configHealth.scanner.backend} — {configHealth.scanner.status}
                      </span>
                    </span>
                  </li>
                  <li className="document-row">
                    <span className="document-title">
                      <span style={{ fontWeight: 600 }}>Email sender</span>
                      <span className="cell-secondary">
                        {configHealth.sender_configured
                          ? "configured and verified in deployment settings"
                          : "not configured — sending disabled"}
                      </span>
                    </span>
                  </li>
                </ul>
              </Card>
            )}
          </>
        )}

        {section === "documents" && !docsReady && (
          <p className="page-status" role="status">
            Loading document settings…
          </p>
        )}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------------- */
/* Pieces                                                                   */
/* ----------------------------------------------------------------------- */

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function SaveBar({
  dirty,
  saving,
  label,
  onSave,
}: {
  dirty: boolean;
  saving: boolean;
  label: string;
  onSave: () => void;
}) {
  return (
    <div
      className="button-row"
      style={{
        justifyContent: "space-between",
        marginTop: "1rem",
        paddingTop: "1rem",
        borderTop: "1px solid var(--border)",
      }}
    >
      <span className="form-help">{dirty ? "Unsaved changes" : " "}</span>
      <Button variant="primary" disabled={!dirty || saving} onClick={onSave}>
        {saving ? "Saving…" : label}
      </Button>
    </div>
  );
}

function TemplateField({
  id,
  label,
  value,
  onChange,
  variables,
  disabled = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  variables: string[];
  disabled?: boolean;
}) {
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        rows={3}
        maxLength={SMS_LIMIT}
        disabled={disabled}
        aria-describedby={`${id}-help`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <p className="char-count">
        {value.length} / {SMS_LIMIT}
      </p>
      <div className="variable-chips" aria-label={`Variables for ${label}`}>
        {variables.map((name) => (
          <button
            key={name}
            type="button"
            disabled={disabled}
            onClick={() => onChange(`${value}{{${name}}}`)}
          >
            {`{{${name}}}`}
          </button>
        ))}
      </div>
      <p id={`${id}-help`} className="form-help">
        {disabled
          ? "Turn the toggle above on to send this message. The template stays editable once enabled."
          : "Click a variable to add it. Any other variable is rejected."}
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------------- */
/* Business hours editor                                                    */
/* ----------------------------------------------------------------------- */

type DayHours = { open: boolean; from: string; to: string };

function toDayHours(hours: Record<string, string[][]> | null): Record<string, DayHours> {
  const result: Record<string, DayHours> = {};
  for (const key of WEEKDAY_KEYS) {
    const first = hours?.[key]?.[0];
    result[key] = first
      ? { open: true, from: first[0], to: first[1] }
      : { open: false, from: "09:00", to: "17:00" };
  }
  return result;
}

/**
 * One opening window per weekday — what a small service business needs. The
 * API accepts several windows per day; any extra windows set elsewhere are
 * preserved rather than silently flattened.
 */
function BusinessHoursEditor({
  hours,
  onChange,
}: {
  hours: Record<string, string[][]> | null;
  onChange: (hours: Record<string, string[][]>) => void;
}) {
  const dayHours = toDayHours(hours);

  function updateDay(key: string, changes: Partial<DayHours>) {
    const next = { ...dayHours, [key]: { ...dayHours[key], ...changes } };
    const value: Record<string, string[][]> = {};
    for (const weekday of WEEKDAY_KEYS) {
      const day = next[weekday];
      if (!day.open) {
        value[weekday] = [];
        continue;
      }
      const extra = (hours?.[weekday] ?? []).slice(1);
      value[weekday] = [[day.from, day.to], ...extra];
    }
    onChange(value);
  }

  return (
    <div>
      {WEEKDAY_KEYS.map((key) => (
        <div key={key} className="business-hours-row">
          <label htmlFor={`hours-${key}-open`}>
            <input
              id={`hours-${key}-open`}
              type="checkbox"
              checked={dayHours[key].open}
              onChange={(event) => updateDay(key, { open: event.target.checked })}
            />{" "}
            {WEEKDAY_LABELS[key]}
          </label>
          <input
            type="time"
            aria-label={`${WEEKDAY_LABELS[key]} opens`}
            disabled={!dayHours[key].open}
            value={dayHours[key].from}
            onChange={(event) => updateDay(key, { from: event.target.value })}
          />
          <input
            type="time"
            aria-label={`${WEEKDAY_LABELS[key]} closes`}
            disabled={!dayHours[key].open}
            value={dayHours[key].to}
            onChange={(event) => updateDay(key, { to: event.target.value })}
          />
        </div>
      ))}
    </div>
  );
}
