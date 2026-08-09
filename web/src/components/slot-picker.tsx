"use client";

import { useEffect, useState } from "react";
import { api, type AvailabilityDay } from "@/lib/api";
import { addDays, dayKeyInZone, formatTimeInZone } from "@/lib/datetime";

/**
 * Offers the times a staff member is actually free on one business-local day.
 *
 * Availability is advisory — the server re-checks it inside the booking
 * transaction — but showing only free times keeps people from choosing a slot
 * that is about to be refused.
 */
export function SlotPicker({
  idPrefix,
  timezone,
  staffId,
  durationMinutes,
  excludeAppointmentId,
  value,
  onChange,
}: {
  idPrefix: string;
  timezone: string;
  staffId: string | null;
  durationMinutes: number | null;
  excludeAppointmentId?: string;
  value: string;
  onChange: (startAtIso: string) => void;
}) {
  const [day, setDay] = useState(() => dayKeyInZone(new Date().toISOString(), timezone));
  // The answer is tagged with the question it answers, so a stale response for
  // a previous day can never be shown as this day's availability.
  const requestKey = [day, staffId ?? "", durationMinutes ?? "", excludeAppointmentId ?? ""].join(
    "|",
  );
  const [loaded, setLoaded] = useState<{ key: string; availability: AvailabilityDay | null }>({
    key: "",
    availability: null,
  });

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ day });
    if (staffId) params.set("staff_id", staffId);
    if (durationMinutes) params.set("duration_minutes", String(durationMinutes));
    // While rescheduling, the appointment's own time is not a conflict.
    if (excludeAppointmentId) params.set("exclude_appointment_id", excludeAppointmentId);
    void api<AvailabilityDay>(`/appointments/availability?${params.toString()}`).then((result) => {
      if (cancelled) return;
      setLoaded({
        key: requestKey,
        availability: result.ok && result.data !== null ? result.data : null,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [requestKey, day, staffId, durationMinutes, excludeAppointmentId]);

  const loading = loaded.key !== requestKey;
  const slots = loading ? [] : (loaded.availability?.slots ?? []);

  return (
    <div className="slot-picker">
      <div className="form-field">
        <label htmlFor={`${idPrefix}-day`}>Day</label>
        <div className="button-row">
          <button type="button" onClick={() => setDay((current) => addDays(current, -1))}>
            Previous day
          </button>
          <input
            id={`${idPrefix}-day`}
            type="date"
            value={day}
            onChange={(event) => setDay(event.target.value)}
          />
          <button type="button" onClick={() => setDay((current) => addDays(current, 1))}>
            Next day
          </button>
        </div>
      </div>

      <fieldset className="slot-list">
        <legend>Available times ({timezone})</legend>
        {loading ? (
          <p className="page-status" role="status">
            Loading times…
          </p>
        ) : slots.length === 0 ? (
          <p role="status">No free times on this day. Try another day.</p>
        ) : (
          <div className="slot-options">
            {slots.map((slot) => (
              <label key={slot} className={slot === value ? "slot-option selected" : "slot-option"}>
                <input
                  type="radio"
                  name={`${idPrefix}-slot`}
                  value={slot}
                  checked={slot === value}
                  onChange={() => onChange(slot)}
                />
                {formatTimeInZone(slot, timezone)}
              </label>
            ))}
          </div>
        )}
      </fieldset>
    </div>
  );
}
