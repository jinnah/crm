"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { BrandMark, useBranding } from "@/components/brand-mark";

/**
 * The customer's own view of one appointment.
 *
 * It is reached only with the capability issued when the appointment was
 * booked, and shows the time, not the CRM record behind it.
 */

type AvailabilityDay = {
  date: string;
  timezone: string;
  duration_minutes: number;
  slots: string[];
};

type PublicAppointment = {
  business_name: string;
  staff_display_name: string | null;
  booking_reference: string;
  start_at: string;
  end_at: string;
  timezone: string;
  status: string;
  can_change: boolean;
  revision: number;
  days: AvailabilityDay[];
};

const STATUS_TEXT: Record<string, string> = {
  scheduled: "Confirmed",
  completed: "Completed",
  canceled: "Canceled",
  no_show: "Missed",
};

function inZone(iso: string, timeZone: string, options: Intl.DateTimeFormatOptions): string {
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone }).format(value);
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(value);
  }
}

export default function PublicAppointmentPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const endpoint = `/api/public-appointment/${encodeURIComponent(token)}`;
  const branding = useBranding();

  const [appointment, setAppointment] = useState<PublicAppointment | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [moving, setMoving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(endpoint, { headers: { Accept: "application/json" } })
      .then(async (response) => {
        const data = (await response.json().catch(() => ({}))) as PublicAppointment & {
          detail?: string;
        };
        if (cancelled) return;
        if (!response.ok) {
          setLoadError(data.detail ?? "This appointment link is not valid.");
          return;
        }
        setAppointment(data);
      })
      .catch(() => {
        if (!cancelled) setLoadError("We could not load your appointment. Please try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [endpoint]);

  const submit = useCallback(
    async (body: Record<string, unknown>, success: string) => {
      setError(null);
      setNotice(null);
      setBusy(true);
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = (await response.json().catch(() => ({}))) as PublicAppointment & {
          detail?: string;
        };
        if (!response.ok) {
          setError(data.detail ?? "That change could not be made. Please contact us.");
          return;
        }
        setAppointment(data);
        setMoving(false);
        setSelected("");
        setNotice(success);
      } catch {
        setError("We could not reach the booking service. Please try again.");
      } finally {
        setBusy(false);
      }
    },
    [endpoint],
  );

  const shell = (children: ReactNode) => (
    <main className="public-form">
      <div className="public-card">
        <div className="public-brand">
          <BrandMark branding={branding} />
          <span className="public-brand-name">
            {appointment?.business_name ?? branding?.business_name ?? ""}
          </span>
        </div>
        {children}
      </div>
    </main>
  );

  if (loadError !== null) {
    return shell(
      <>
        <h1>Appointment unavailable</h1>
        <p role="alert">{loadError}</p>
        <p>Please contact us and we will help.</p>
      </>,
    );
  }

  if (appointment === null) {
    return shell(
      <p className="page-status" role="status">
        Loading your appointment…
      </p>,
    );
  }

  return shell(
    <>
        <h1>Your appointment with {appointment.business_name}</h1>
        <div className="public-summary">
          <strong>
            {inZone(appointment.start_at, appointment.timezone, {
              weekday: "long",
              day: "numeric",
              month: "long",
              hour: "2-digit",
              minute: "2-digit",
              timeZoneName: "short",
            })}
          </strong>
          <span>
            {STATUS_TEXT[appointment.status] ?? appointment.status}
            {appointment.staff_display_name !== null && (
              <> · with {appointment.staff_display_name}</>
            )}
            {appointment.booking_reference !== "" && (
              <> · reference {appointment.booking_reference}</>
            )}
          </span>
        </div>

        {notice !== null && (
          <p className="form-success" role="status">
            {notice}
          </p>
        )}
        {error !== null && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        {appointment.status === "canceled" && (
          <p>This appointment is canceled. Contact us if you would like a new time.</p>
        )}

        {appointment.can_change && (
          <div className="button-row">
            <button type="button" disabled={busy} onClick={() => setMoving((open) => !open)}>
              {moving ? "Keep this time" : "Change time"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void submit({ action: "cancel" }, "Your appointment is canceled.")}
            >
              Cancel appointment
            </button>
          </div>
        )}

        {moving && appointment.can_change && (
          <>
            {appointment.days.length === 0 ? (
              <p role="status">
                There are no other free times at the moment. Please contact us.
              </p>
            ) : (
              appointment.days.map((day) => (
                <fieldset key={day.date} className="slot-list">
                  <legend>
                    {inZone(`${day.date}T12:00:00Z`, "UTC", {
                      weekday: "long",
                      day: "numeric",
                      month: "long",
                    })}
                  </legend>
                  <div className="slot-options">
                    {day.slots.map((slot) => (
                      <label
                        key={slot}
                        className={slot === selected ? "slot-option selected" : "slot-option"}
                      >
                        <input
                          type="radio"
                          name="new-slot"
                          value={slot}
                          checked={slot === selected}
                          onChange={() => setSelected(slot)}
                        />
                        {inZone(slot, day.timezone, { hour: "2-digit", minute: "2-digit" })}
                      </label>
                    ))}
                  </div>
                </fieldset>
              ))
            )}

            {/* Honeypot: hidden from people, tempting to bots. */}
            <div className="honeypot" aria-hidden="true">
              <label htmlFor="manage-website">Leave this field empty</label>
              <input
                id="manage-website"
                tabIndex={-1}
                autoComplete="off"
                value={website}
                onChange={(event) => setWebsite(event.target.value)}
              />
            </div>

            {appointment.days.length > 0 && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (!selected) {
                    setError("Choose a new time first.");
                    return;
                  }
                  void submit(
                    {
                      action: "reschedule",
                      start_at: selected,
                      expected_revision: appointment.revision,
                      website,
                    },
                    "Your appointment has been moved.",
                  );
                }}
              >
                {busy ? "Moving…" : "Confirm new time"}
              </button>
            )}
          </>
        )}
    </>,
  );
}
