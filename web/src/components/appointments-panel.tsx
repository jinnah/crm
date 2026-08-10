"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SlotPicker } from "@/components/slot-picker";
import {
  api,
  apiUrl,
  errorDetail,
  APPOINTMENT_STATUS_LABELS,
  type Appointment,
  type AssignableUser,
  type BookingLink,
  type Lead,
  type SchedulingBasics,
} from "@/lib/api";
import { formatDateTime, formatInZone } from "@/lib/datetime";

const DURATION_CHOICES = [15, 30, 45, 60, 90, 120, 180];

/** Appointments, booking links and their history for one lead. */
export function AppointmentsPanel({
  lead,
  csrfToken,
  canSchedule,
  users,
  onChanged,
}: {
  lead: Lead;
  csrfToken: string;
  canSchedule: boolean;
  users: AssignableUser[];
  onChanged: () => void;
}) {
  const [appointments, setAppointments] = useState<Appointment[] | null>(null);
  const [settings, setSettings] = useState<SchedulingBasics | null>(null);
  const [link, setLink] = useState<BookingLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void api<Appointment[]>(`/leads/${lead.id}/appointments`).then((result) => {
      if (!cancelled && result.ok && Array.isArray(result.data)) setAppointments(result.data);
      else if (!cancelled && !result.ok) setAppointments([]);
    });
    void api<BookingLink | null>(`/leads/${lead.id}/booking-link`).then((result) => {
      if (!cancelled && result.ok) setLink(result.data ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, [lead.id, reloadNonce]);

  useEffect(() => {
    let cancelled = false;
    void api<SchedulingBasics>("/settings/scheduling-basics").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setSettings(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const reload = useCallback(() => {
    setReloadNonce((value) => value + 1);
    onChanged();
  }, [onChanged]);

  const act = useCallback(
    async (path: string, body: unknown, successMessage: string) => {
      setError(null);
      setNotice(null);
      const result = await api(path, { method: "POST", csrfToken, body });
      if (!result.ok) {
        setError(errorDetail(result.data, "The appointment could not be updated."));
        return false;
      }
      setNotice(successMessage);
      reload();
      return true;
    },
    [csrfToken, reload],
  );

  const archived = lead.archived_at !== null;
  const timezone = settings?.business_timezone ?? "UTC";
  const upcoming = (appointments ?? [])
    .filter((item) => item.status === "scheduled" && new Date(item.end_at) >= new Date())
    .sort((a, b) => a.start_at.localeCompare(b.start_at));

  return (
    <section className="appointments-panel">
      <h2>Appointments</h2>

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

      {appointments === null ? (
        <p className="page-status" role="status">
          Loading appointments…
        </p>
      ) : (
        <>
          <div className="appointment-next">
            <h3>Next appointment</h3>
            {upcoming.length === 0 ? (
              <p>No upcoming appointment.</p>
            ) : (
              <p>
                <strong>{upcoming[0].subject}</strong> · {formatInZone(upcoming[0].start_at, upcoming[0].timezone)}
                {upcoming[0].assignee_email !== null && <> · {upcoming[0].assignee_email}</>}
              </p>
            )}
          </div>

          {canSchedule && !archived && settings !== null && (
            <CreateAppointmentForm
              lead={lead}
              settings={settings}
              users={users}
              onCreate={(body) =>
                act(`/leads/${lead.id}/appointments`, body, "Appointment scheduled.")
              }
            />
          )}
          {archived && <p className="form-help">Restore this lead to schedule appointments.</p>}

          <AppointmentHistory
            appointments={appointments}
            timezone={timezone}
            canSchedule={canSchedule && !archived}
            settings={settings}
            onAct={act}
            onError={setError}
          />
        </>
      )}

      {canSchedule && (
        <BookingLinkControls
          lead={lead}
          link={link}
          users={users}
          csrfToken={csrfToken}
          disabled={archived}
          selfBookingEnabled={settings?.self_booking_enabled ?? false}
          onChanged={reload}
          onError={setError}
          onNotice={setNotice}
        />
      )}
    </section>
  );
}

function CreateAppointmentForm({
  lead,
  settings,
  users,
  onCreate,
}: {
  lead: Lead;
  settings: SchedulingBasics;
  users: AssignableUser[];
  onCreate: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  const [subject, setSubject] = useState("Appointment");
  const [notes, setNotes] = useState("");
  const [duration, setDuration] = useState(settings.appointment_duration_minutes);
  const [staffId, setStaffId] = useState<string>(lead.assigned_to ?? "");
  const [startAt, setStartAt] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!startAt) {
      setValidation("Choose an available time first.");
      return;
    }
    setValidation(null);
    setSaving(true);
    const created = await onCreate({
      start_at: startAt,
      duration_minutes: duration,
      subject: subject.trim() || "Appointment",
      notes: notes.trim(),
      assigned_to: staffId === "" ? null : staffId,
    });
    setSaving(false);
    if (created) {
      setStartAt("");
      setNotes("");
    }
  }

  return (
    <form className="appointment-form" onSubmit={handleSubmit} noValidate>
      <h3>Schedule an appointment</h3>
      <div className="form-field">
        <label htmlFor="appointment-subject">Subject</label>
        <input
          id="appointment-subject"
          value={subject}
          maxLength={200}
          onChange={(event) => setSubject(event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="appointment-staff">Assigned to</label>
        <select
          id="appointment-staff"
          value={staffId}
          onChange={(event) => {
            setStaffId(event.target.value);
            setStartAt("");
          }}
        >
          <option value="">Unassigned</option>
          {users.map((option) => (
            <option key={option.id} value={option.id}>
              {option.email}
            </option>
          ))}
        </select>
      </div>
      <div className="form-field">
        <label htmlFor="appointment-duration">Duration</label>
        <select
          id="appointment-duration"
          value={duration}
          onChange={(event) => {
            setDuration(Number(event.target.value));
            setStartAt("");
          }}
        >
          {[...new Set([settings.appointment_duration_minutes, ...DURATION_CHOICES])]
            .sort((a, b) => a - b)
            .map((value) => (
              <option key={value} value={value}>
                {value} minutes
              </option>
            ))}
        </select>
      </div>

      <SlotPicker
        idPrefix="create"
        timezone={settings.business_timezone}
        staffId={staffId === "" ? null : staffId}
        durationMinutes={duration}
        value={startAt}
        onChange={setStartAt}
      />

      <div className="form-field">
        <label htmlFor="appointment-notes">Notes</label>
        <textarea
          id="appointment-notes"
          rows={2}
          maxLength={2000}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
      </div>

      {validation !== null && (
        <p className="form-error" role="alert">
          {validation}
        </p>
      )}
      <button type="submit" disabled={saving}>
        {saving ? "Scheduling…" : "Schedule appointment"}
      </button>
    </form>
  );
}

function AppointmentHistory({
  appointments,
  timezone,
  canSchedule,
  settings,
  onAct,
  onError,
}: {
  appointments: Appointment[];
  timezone: string;
  canSchedule: boolean;
  settings: SchedulingBasics | null;
  onAct: (path: string, body: unknown, successMessage: string) => Promise<boolean>;
  onError: (message: string) => void;
}) {
  const [rescheduling, setRescheduling] = useState<string | null>(null);

  return (
    <div className="appointment-history">
      <h3>Appointment history</h3>
      {appointments.length === 0 ? (
        <p>No appointments yet.</p>
      ) : (
        <ul className="appointment-list">
          {appointments.map((appointment) => (
            <li key={appointment.id} className={`appointment-item status-${appointment.status}`}>
              <p className="appointment-when">
                <strong>{appointment.subject}</strong>
                {" · "}
                {formatInZone(appointment.start_at, appointment.timezone)}
                {" – "}
                {formatInZone(appointment.end_at, appointment.timezone)}
              </p>
              <p className="appointment-meta">
                {APPOINTMENT_STATUS_LABELS[appointment.status] ?? appointment.status}
                {appointment.origin === "customer" && " · booked online"}
                {appointment.assignee_email !== null && ` · ${appointment.assignee_email}`}
                {appointment.booking_reference !== null && ` · ${appointment.booking_reference}`}
              </p>
              {appointment.notes !== "" && <p className="appointment-notes">{appointment.notes}</p>}
              {appointment.cancellation_reason !== null && (
                <p className="appointment-notes">Reason: {appointment.cancellation_reason}</p>
              )}

              <div className="button-row">
                <button
                  type="button"
                  onClick={() => void downloadIcs(appointment, onError)}
                >
                  Download .ics
                </button>
                {canSchedule && appointment.status === "scheduled" && (
                  <>
                    <button
                      type="button"
                      onClick={() =>
                        setRescheduling((current) =>
                          current === appointment.id ? null : appointment.id,
                        )
                      }
                    >
                      {rescheduling === appointment.id ? "Cancel reschedule" : "Reschedule"}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void onAct(
                          `/appointments/${appointment.id}/disposition`,
                          {
                            status: "completed",
                            expected_revision: appointment.revision,
                          },
                          "Appointment completed.",
                        );
                      }}
                    >
                      Mark completed
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void onAct(
                          `/appointments/${appointment.id}/disposition`,
                          {
                            status: "no_show",
                            expected_revision: appointment.revision,
                          },
                          "Appointment marked no-show.",
                        );
                      }}
                    >
                      Mark no-show
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void onAct(
                          `/appointments/${appointment.id}/disposition`,
                          {
                            status: "canceled",
                            expected_revision: appointment.revision,
                          },
                          "Appointment canceled. The customer has been told.",
                        );
                      }}
                    >
                      Cancel appointment
                    </button>
                  </>
                )}
              </div>

              {rescheduling === appointment.id && settings !== null && (
                <RescheduleForm
                  appointment={appointment}
                  timezone={timezone}
                  onSubmit={async (body) => {
                    const ok = await onAct(
                      `/appointments/${appointment.id}/reschedule`,
                      body,
                      "Appointment moved. The customer has been told.",
                    );
                    if (ok) setRescheduling(null);
                    return ok;
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RescheduleForm({
  appointment,
  timezone,
  onSubmit,
}: {
  appointment: Appointment;
  timezone: string;
  onSubmit: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  const currentDuration = Math.round(
    (new Date(appointment.end_at).getTime() - new Date(appointment.start_at).getTime()) / 60_000,
  );
  const [startAt, setStartAt] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  return (
    <form
      className="appointment-reschedule"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (!startAt) {
          setValidation("Choose a new time first.");
          return;
        }
        setValidation(null);
        setSaving(true);
        void onSubmit({
          start_at: startAt,
          duration_minutes: currentDuration,
          expected_revision: appointment.revision,
        }).finally(() => setSaving(false));
      }}
    >
      <h4>Move this appointment</h4>
      <SlotPicker
        idPrefix={`reschedule-${appointment.id}`}
        timezone={timezone}
        staffId={appointment.assigned_to}
        durationMinutes={currentDuration}
        excludeAppointmentId={appointment.id}
        value={startAt}
        onChange={setStartAt}
      />
      {validation !== null && (
        <p className="form-error" role="alert">
          {validation}
        </p>
      )}
      <button type="submit" disabled={saving}>
        {saving ? "Moving…" : "Confirm new time"}
      </button>
    </form>
  );
}

/**
 * Calendar files are fetched with the session cookie and handed to the browser
 * as a blob, so the download never leaves the authenticated session.
 */
async function downloadIcs(appointment: Appointment, onError: (message: string) => void) {
  try {
    const response = await fetch(apiUrl(`/appointments/${appointment.id}/calendar.ics`), {
      credentials: "include",
    });
    if (!response.ok) {
      onError("The calendar file could not be downloaded.");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `appointment-${appointment.id}.ics`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  } catch {
    onError("The calendar file could not be downloaded.");
  }
}

function BookingLinkControls({
  lead,
  link,
  users,
  csrfToken,
  disabled,
  selfBookingEnabled,
  onChanged,
  onError,
  onNotice,
}: {
  lead: Lead;
  link: BookingLink | null;
  users: AssignableUser[];
  csrfToken: string;
  disabled: boolean;
  selfBookingEnabled: boolean;
  onChanged: () => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [staffId, setStaffId] = useState<string>(lead.assigned_to ?? "");
  // The raw link exists only in this component's memory, for this one render.
  const [freshUrl, setFreshUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(regenerating: boolean) {
    setBusy(true);
    setFreshUrl(null);
    const result = await api<BookingLink>(`/leads/${lead.id}/booking-link`, {
      method: "POST",
      csrfToken,
      body: { assigned_to: staffId === "" ? null : staffId },
    });
    setBusy(false);
    if (!result.ok || result.data === null) {
      onError(errorDetail(result.data, "The booking link could not be created."));
      return;
    }
    setFreshUrl(result.data.url);
    onNotice(
      regenerating
        ? "A new booking link was created. The previous link no longer works."
        : "Booking link created. Copy it now — it is shown only once.",
    );
    onChanged();
  }

  async function revoke() {
    setBusy(true);
    const result = await api(`/leads/${lead.id}/booking-link/revoke`, {
      method: "POST",
      csrfToken,
    });
    setBusy(false);
    if (!result.ok) {
      onError(errorDetail(result.data, "The booking link could not be revoked."));
      return;
    }
    setFreshUrl(null);
    onNotice("Booking link revoked. It can no longer be used.");
    onChanged();
  }

  const active = link !== null && link.revoked_at === null;

  return (
    <div className="booking-link">
      <h3>Customer booking link</h3>
      {!selfBookingEnabled && (
        <p className="form-help">
          Online booking is turned off in settings, so a link will not open for the customer.
        </p>
      )}
      <div className="form-field">
        <label htmlFor="booking-link-staff">Book with</label>
        <select
          id="booking-link-staff"
          value={staffId}
          disabled={disabled || busy}
          onChange={(event) => setStaffId(event.target.value)}
        >
          <option value="">Anyone</option>
          {users.map((option) => (
            <option key={option.id} value={option.id}>
              {option.email}
            </option>
          ))}
        </select>
      </div>

      {active ? (
        <p>
          A link is active until {formatDateTime(link.expires_at)}
          {link.last_used_at !== null && <> · last used {formatDateTime(link.last_used_at)}</>}.
        </p>
      ) : (
        <p>No active booking link.</p>
      )}

      {freshUrl !== null && (
        <div className="booking-link-value">
          <label htmlFor="booking-link-url">Booking link (shown once)</label>
          <input id="booking-link-url" readOnly value={freshUrl} />
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard
                ?.writeText(freshUrl)
                .then(() => onNotice("Booking link copied."))
                .catch(() => onError("Copy the link manually — the clipboard is unavailable."));
            }}
          >
            Copy link
          </button>
        </div>
      )}

      <div className="button-row">
        <button type="button" disabled={disabled || busy} onClick={() => void create(active)}>
          {active ? "Regenerate link" : "Create booking link"}
        </button>
        {active && (
          <button type="button" disabled={disabled || busy} onClick={() => void revoke()}>
            Revoke link
          </button>
        )}
      </div>
      <p className="form-help">
        The link is stored only as a hash — it cannot be shown again later. Regenerating replaces
        the previous link.
      </p>
    </div>
  );
}
