"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * Public booking page.
 *
 * Everything it knows comes from the token in the URL, forwarded by the
 * same-origin route. No lead identifier, no contact details and no CRM data
 * ever reach this page, and the token is never written to storage.
 */

type AvailabilityDay = {
  date: string;
  timezone: string;
  duration_minutes: number;
  slots: string[];
};

type BookingInfo = {
  business_name: string;
  intro: string;
  staff_display_name: string | null;
  duration_minutes: number;
  timezone: string;
  days: AvailabilityDay[];
};

type BookingResult = {
  booking_reference: string;
  start_at: string;
  end_at: string;
  timezone: string;
  // Capability for changing or canceling this one appointment. It is shown
  // once, on this screen, and is never stored by the page.
  manage_token?: string | null;
  duplicate?: boolean;
};

function newBookingKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `book-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

function inZone(iso: string, timeZone: string, options: Intl.DateTimeFormatOptions): string {
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone }).format(value);
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(value);
  }
}

export default function PublicBookingPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;

  const [info, setInfo] = useState<BookingInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  // One key for this visitor's booking attempt: a retry books the same slot
  // once instead of creating a second appointment.
  const [bookingKey] = useState(newBookingKey);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<BookingResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/public-booking/${encodeURIComponent(token)}`, {
      headers: { Accept: "application/json" },
    })
      .then(async (response) => {
        const data = (await response.json().catch(() => ({}))) as BookingInfo & {
          detail?: string;
        };
        if (cancelled) return;
        if (!response.ok) {
          setLoadError(data.detail ?? "This booking link is not valid.");
          return;
        }
        setInfo(data);
      })
      .catch(() => {
        if (!cancelled) setLoadError("We could not load the booking page. Please try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function book() {
    if (!selected) {
      setError("Choose a time first.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const response = await fetch(`/api/public-booking/${encodeURIComponent(token)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_at: selected, booking_key: bookingKey, website }),
      });
      const data = (await response.json().catch(() => ({}))) as BookingResult & {
        detail?: string;
      };
      if (!response.ok) {
        setError(data.detail ?? "That time could not be booked. Please choose another.");
        return;
      }
      setResult(data);
    } catch {
      setError("We could not reach the booking service. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loadError !== null) {
    return (
      <main className="public-form">
        <div className="public-card">
          <h1>Booking unavailable</h1>
          <p role="alert">{loadError}</p>
          <p>Please contact us and we will arrange a new time.</p>
        </div>
      </main>
    );
  }

  if (result !== null) {
    return (
      <main className="public-form">
        <div className="public-card">
          <h1>{result.duplicate ? "You are already booked" : "Appointment confirmed"}</h1>
          <p role="status">
            {inZone(result.start_at, result.timezone, {
              weekday: "long",
              day: "numeric",
              month: "long",
              hour: "2-digit",
              minute: "2-digit",
              timeZoneName: "short",
            })}
          </p>
          <p>
            Your reference is <strong>{result.booking_reference}</strong>.
          </p>
          <p>We will send a confirmation by text message.</p>
          {result.manage_token ? (
            <p>
              <a href={`/appointment/${encodeURIComponent(result.manage_token)}`}>
                Change or cancel this appointment
              </a>{" "}
              — keep this link, it is the only way to manage the booking.
            </p>
          ) : null}
        </div>
      </main>
    );
  }

  if (info === null) {
    return (
      <main className="public-form">
        <div className="public-card">
          <p className="page-status" role="status">
            Loading available times…
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="public-form">
      <div className="public-card">
        <h1>Book with {info.business_name}</h1>
        {info.intro ? <p>{info.intro}</p> : null}
        <p>
          {info.duration_minutes} minutes
          {info.staff_display_name !== null && <> with {info.staff_display_name}</>} · times shown
          in {info.timezone}
        </p>

        {info.days.length === 0 ? (
          <p role="status">
            There are no free times at the moment. Please contact us and we will find one.
          </p>
        ) : (
          info.days.map((day) => (
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
                      name="booking-slot"
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
          <label htmlFor="booking-website">Leave this field empty</label>
          <input
            id="booking-website"
            tabIndex={-1}
            autoComplete="off"
            value={website}
            onChange={(event) => setWebsite(event.target.value)}
          />
        </div>

        {error !== null && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {info.days.length > 0 && (
          <button type="button" disabled={submitting} onClick={() => void book()}>
            {submitting ? "Booking…" : "Confirm booking"}
          </button>
        )}
      </div>
    </main>
  );
}
