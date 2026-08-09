"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  errorDetail,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS,
  type SchedulingSettings,
} from "@/lib/api";

const APPOINTMENT_TEMPLATE_VARIABLES = [
  "lead_name",
  "business_name",
  "appointment_date",
  "appointment_time",
  "assigned_staff",
  "appointment_subject",
  "booking_reference",
];

type DayHours = { open: boolean; from: string; to: string };

/**
 * The editor covers one opening window per weekday, which is what a small
 * service business needs. The API accepts several windows per day, so hours
 * set elsewhere are preserved rather than being flattened silently.
 */
function toDayHours(hours: Record<string, string[][]> | null): Record<string, DayHours> {
  const result: Record<string, DayHours> = {};
  for (const key of WEEKDAY_KEYS) {
    const windows = hours?.[key] ?? [];
    const first = windows[0];
    result[key] = first
      ? { open: true, from: first[0], to: first[1] }
      : { open: false, from: "09:00", to: "17:00" };
  }
  return result;
}

function fromDayHours(
  edited: Record<string, DayHours>,
  original: Record<string, string[][]> | null,
): Record<string, string[][]> {
  const result: Record<string, string[][]> = {};
  for (const key of WEEKDAY_KEYS) {
    const day = edited[key];
    if (!day.open) {
      result[key] = [];
      continue;
    }
    const extra = (original?.[key] ?? []).slice(1);
    result[key] = [[day.from, day.to], ...extra];
  }
  return result;
}

export function SchedulingSettingsForm({ csrfToken }: { csrfToken: string }) {
  const [settings, setSettings] = useState<SchedulingSettings | null>(null);
  const [hours, setHours] = useState<Record<string, DayHours>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void api<SchedulingSettings>("/settings/scheduling").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load scheduling settings."));
        return;
      }
      setSettings(result.data);
      setHours(toDayHours(result.data.business_hours));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  function update<K extends keyof SchedulingSettings>(key: K, value: SchedulingSettings[K]) {
    setSettings((previous) => (previous === null ? previous : { ...previous, [key]: value }));
  }

  function updateDay(key: string, changes: Partial<DayHours>) {
    setHours((previous) => ({ ...previous, [key]: { ...previous[key], ...changes } }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (settings === null) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    const result = await api<SchedulingSettings>("/settings/scheduling", {
      method: "PATCH",
      csrfToken,
      body: { ...settings, business_hours: fromDayHours(hours, settings.business_hours) },
    });
    setSaving(false);
    if (!result.ok || result.data === null) {
      setError(errorDetail(result.data, "Unable to save scheduling settings."));
      return;
    }
    setSettings(result.data);
    setHours(toDayHours(result.data.business_hours));
    setNotice("Scheduling settings saved.");
  }

  if (settings === null) {
    return (
      <p className="page-status" role="status">
        {error ?? "Loading scheduling settings…"}
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <h2>Scheduling</h2>
      <div className="form-field">
        <label htmlFor="business-timezone">Business time zone</label>
        <input
          id="business-timezone"
          aria-describedby="timezone-help"
          value={settings.business_timezone}
          onChange={(event) => update("business_timezone", event.target.value)}
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
          value={settings.appointment_duration_minutes}
          onChange={(event) =>
            update("appointment_duration_minutes", Number(event.target.value) || 5)
          }
        />
      </div>
      <div className="form-field">
        <label htmlFor="min-notice">Minimum booking notice (minutes)</label>
        <input
          id="min-notice"
          type="number"
          min={0}
          value={settings.min_booking_notice_minutes}
          onChange={(event) => update("min_booking_notice_minutes", Number(event.target.value) || 0)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="max-ahead">Bookable how far ahead (days)</label>
        <input
          id="max-ahead"
          type="number"
          min={1}
          max={365}
          value={settings.max_booking_days_ahead}
          onChange={(event) => update("max_booking_days_ahead", Number(event.target.value) || 1)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="buffer-before">Buffer before an appointment (minutes)</label>
        <input
          id="buffer-before"
          type="number"
          min={0}
          max={240}
          value={settings.buffer_before_minutes}
          onChange={(event) => update("buffer_before_minutes", Number(event.target.value) || 0)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="buffer-after">Buffer after an appointment (minutes)</label>
        <input
          id="buffer-after"
          type="number"
          min={0}
          max={240}
          value={settings.buffer_after_minutes}
          onChange={(event) => update("buffer_after_minutes", Number(event.target.value) || 0)}
        />
      </div>

      <h3>Business hours</h3>
      <p className="form-help">One opening window per day. Clear a day to close it.</p>
      {WEEKDAY_KEYS.map((key) => (
        <div key={key} className="form-field business-hours-row">
          <label htmlFor={`hours-${key}-open`}>
            <input
              id={`hours-${key}-open`}
              type="checkbox"
              checked={hours[key]?.open ?? false}
              onChange={(event) => updateDay(key, { open: event.target.checked })}
            />{" "}
            {WEEKDAY_LABELS[key]}
          </label>
          <input
            type="time"
            aria-label={`${WEEKDAY_LABELS[key]} opens`}
            disabled={!hours[key]?.open}
            value={hours[key]?.from ?? "09:00"}
            onChange={(event) => updateDay(key, { from: event.target.value })}
          />
          <input
            type="time"
            aria-label={`${WEEKDAY_LABELS[key]} closes`}
            disabled={!hours[key]?.open}
            value={hours[key]?.to ?? "17:00"}
            onChange={(event) => updateDay(key, { to: event.target.value })}
          />
        </div>
      ))}

      <h3>Customer booking</h3>
      <div className="form-field form-field-checkbox">
        <label htmlFor="self-booking">
          <input
            id="self-booking"
            type="checkbox"
            checked={settings.self_booking_enabled}
            onChange={(event) => update("self_booking_enabled", event.target.checked)}
          />{" "}
          Let customers book and change their own appointments with a link
        </label>
      </div>

      <h3>Appointment messages</h3>
      <p className="form-help">
        Templates may use:{" "}
        {APPOINTMENT_TEMPLATE_VARIABLES.map((name) => `{{${name}}}`).join(", ")}. Any other
        variable is rejected.
      </p>
      <div className="form-field form-field-checkbox">
        <label htmlFor="confirmation-enabled">
          <input
            id="confirmation-enabled"
            type="checkbox"
            checked={settings.appointment_confirmation_enabled}
            onChange={(event) =>
              update("appointment_confirmation_enabled", event.target.checked)
            }
          />{" "}
          Send a confirmation when an appointment is booked or canceled
        </label>
      </div>
      <div className="form-field">
        <label htmlFor="confirmation-template">Confirmation message</label>
        <textarea
          id="confirmation-template"
          rows={3}
          value={settings.confirmation_template}
          onChange={(event) => update("confirmation_template", event.target.value)}
        />
      </div>
      <div className="form-field form-field-checkbox">
        <label htmlFor="reminder-enabled">
          <input
            id="reminder-enabled"
            type="checkbox"
            checked={settings.appointment_reminder_enabled}
            onChange={(event) => update("appointment_reminder_enabled", event.target.checked)}
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
          value={settings.reminder_offset_minutes}
          onChange={(event) => update("reminder_offset_minutes", Number(event.target.value) || 5)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="second-reminder-offset">Second reminder, minutes before (optional)</label>
        <input
          id="second-reminder-offset"
          type="number"
          min={5}
          value={settings.second_reminder_offset_minutes ?? ""}
          onChange={(event) =>
            update(
              "second_reminder_offset_minutes",
              event.target.value === "" ? null : Number(event.target.value),
            )
          }
        />
      </div>
      <div className="form-field">
        <label htmlFor="reminder-template">Reminder message</label>
        <textarea
          id="reminder-template"
          rows={3}
          value={settings.reminder_template}
          onChange={(event) => update("reminder_template", event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="rescheduled-template">Rescheduled message</label>
        <textarea
          id="rescheduled-template"
          rows={3}
          value={settings.appointment_rescheduled_template}
          onChange={(event) => update("appointment_rescheduled_template", event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="canceled-template">Cancellation message</label>
        <textarea
          id="canceled-template"
          rows={3}
          value={settings.appointment_canceled_template}
          onChange={(event) => update("appointment_canceled_template", event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="upcoming-window">
          Show appointments in the attention queue this many hours ahead
        </label>
        <input
          id="upcoming-window"
          type="number"
          min={1}
          max={336}
          value={settings.upcoming_window_hours}
          onChange={(event) => update("upcoming_window_hours", Number(event.target.value) || 1)}
        />
      </div>

      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice !== null && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      <button type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save scheduling settings"}
      </button>
    </form>
  );
}
