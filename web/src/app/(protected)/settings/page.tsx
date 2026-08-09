"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-context";
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
  type CommunicationSettings,
  type SchedulingSettings,
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
  | "notifications";

const SECTIONS: ReadonlyArray<{ key: SectionKey; label: string }> = [
  { key: "business", label: "Business & branding" },
  { key: "intake", label: "Lead intake" },
  { key: "messages", label: "Automated messages" },
  { key: "response", label: "Response targets" },
  { key: "scheduling", label: "Scheduling rules" },
  { key: "availability", label: "Availability" },
  { key: "notifications", label: "Appointment notifications" },
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

const MESSAGE_VARIABLES = ["lead_name", "business_name", "source", "lead_id"];
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
  const [comm, setComm] = useState<CommunicationSettings | null>(null);
  const [sched, setSched] = useState<SchedulingSettings | null>(null);
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
    return () => {
      cancelled = true;
    };
  }, [isOwner]);

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
    return dirty;
  }, [comm, savedComm, sched, savedSched]);

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

  const schedReady = sched !== null && savedSched !== null;
  const currentLabel = SECTIONS.find((entry) => entry.key === section)?.label ?? "Settings";

  return (
    <section>
      <PageHeader
        title="Settings"
        description="How your business presents itself, captures leads and runs its schedule."
      />

      <SectionNav
        sections={SECTIONS}
        active={section}
        onSelect={switchSection}
        label="Settings sections"
      />

      {error !== null && <InlineError>{error}</InlineError>}
      {notice !== null && <InlineSuccess>{notice}</InlineSuccess>}

      <div className="stack narrow-form">
        {section === "business" && (
          <>
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
          </>
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

        {(section === "scheduling" || section === "availability" || section === "notifications") &&
          !schedReady && (
            <p className="page-status" role="status">
              Loading scheduling settings…
            </p>
          )}
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
