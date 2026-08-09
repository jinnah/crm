"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-context";
import {
  api,
  errorDetail,
  APPOINTMENT_STATUS_LABELS,
  type Appointment,
  type AssignableUser,
  type SchedulingBasics,
} from "@/lib/api";
import {
  addDays,
  dayKeyInZone,
  formatDayInZone,
  formatTimeInZone,
  startOfWeek,
} from "@/lib/datetime";

type View = "day" | "week" | "agenda";

const VIEWS: Array<{ key: View; label: string }> = [
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "agenda", label: "Agenda" },
];

/** How many days each view asks the server for, starting at its anchor day. */
const VIEW_SPAN: Record<View, number> = { day: 1, week: 7, agenda: 14 };

export default function CalendarPage() {
  const { user } = useAuth();
  const canManage = user.role === "owner" || user.role === "manager";

  const [basics, setBasics] = useState<SchedulingBasics | null>(null);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  // Results are tagged with the range they belong to, so a slow response for a
  // week you have already navigated away from is never rendered.
  const [loaded, setLoaded] = useState<{ key: string; items: Appointment[] }>({
    key: "",
    items: [],
  });
  const [view, setView] = useState<View>("week");
  const [anchor, setAnchor] = useState<string | null>(null);
  const [staffFilter, setStaffFilter] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api<SchedulingBasics>("/settings/scheduling-basics").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load the calendar."));
        return;
      }
      setBasics(result.data);
      setAnchor(dayKeyInZone(new Date().toISOString(), result.data.business_timezone));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!canManage) return;
    let cancelled = false;
    void api<AssignableUser[]>("/leads/assignable-users").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setUsers(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [canManage]);

  const timezone = basics?.business_timezone ?? "UTC";
  const rangeStart = anchor === null ? null : view === "week" ? startOfWeek(anchor) : anchor;
  const rangeEnd = rangeStart === null ? null : addDays(rangeStart, VIEW_SPAN[view]);

  const rangeKey = `${rangeStart ?? ""}|${rangeEnd ?? ""}|${staffFilter}`;

  useEffect(() => {
    if (rangeStart === null || rangeEnd === null) return;
    let cancelled = false;
    // The day boundaries are business-local; the server compares instants, so
    // a generous margin either side keeps edge appointments visible.
    const params = new URLSearchParams({
      start: `${addDays(rangeStart, -1)}T00:00:00Z`,
      end: `${addDays(rangeEnd, 1)}T00:00:00Z`,
    });
    if (staffFilter) params.set("staff_id", staffFilter);
    void api<Appointment[]>(`/appointments?${params.toString()}`).then((result) => {
      if (cancelled) return;
      if (!result.ok || !Array.isArray(result.data)) {
        setError(errorDetail(result.data, "Unable to load appointments."));
        setLoaded({ key: rangeKey, items: [] });
        return;
      }
      setError(null);
      setLoaded({ key: rangeKey, items: result.data });
    });
    return () => {
      cancelled = true;
    };
  }, [rangeKey, rangeStart, rangeEnd, staffFilter]);

  const appointments = loaded.key === rangeKey ? loaded.items : null;

  const days = useMemo(() => {
    if (rangeStart === null) return [];
    return Array.from({ length: VIEW_SPAN[view] }, (_, index) => addDays(rangeStart, index));
  }, [rangeStart, view]);

  const byDay = useMemo(() => {
    const grouped = new Map<string, Appointment[]>();
    for (const appointment of appointments ?? []) {
      const key = dayKeyInZone(appointment.start_at, timezone);
      const bucket = grouped.get(key);
      if (bucket) bucket.push(appointment);
      else grouped.set(key, [appointment]);
    }
    for (const bucket of grouped.values()) {
      bucket.sort((a, b) => a.start_at.localeCompare(b.start_at));
    }
    return grouped;
  }, [appointments, timezone]);

  if (anchor === null || rangeStart === null) {
    return error !== null ? (
      <section>
        <h1>Calendar</h1>
        <p className="form-error" role="alert">
          {error}
        </p>
      </section>
    ) : (
      <p className="page-status" role="status">
        Loading calendar…
      </p>
    );
  }

  const step = view === "week" ? 7 : view === "agenda" ? 14 : 1;
  const today = dayKeyInZone(new Date().toISOString(), timezone);

  return (
    <section className="calendar-page">
      <div className="page-head">
        <h1>Calendar</h1>
      </div>

      <div className="calendar-controls">
        <div className="button-row" role="group" aria-label="Calendar view">
          {VIEWS.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={view === option.key}
              onClick={() => setView(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="button-row">
          <button type="button" onClick={() => setAnchor((day) => addDays(day ?? today, -step))}>
            Previous
          </button>
          <button type="button" onClick={() => setAnchor(today)}>
            Today
          </button>
          <button type="button" onClick={() => setAnchor((day) => addDays(day ?? today, step))}>
            Next
          </button>
        </div>
        {canManage && (
          <div className="form-field">
            <label htmlFor="calendar-staff">Staff</label>
            <select
              id="calendar-staff"
              value={staffFilter}
              onChange={(event) => setStaffFilter(event.target.value)}
            >
              <option value="">Everyone</option>
              {users.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.email}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <p className="calendar-zone">Times are shown in {timezone}.</p>

      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      {appointments === null ? (
        <p className="page-status" role="status">
          Loading appointments…
        </p>
      ) : view === "agenda" ? (
        <AgendaView appointments={appointments} timezone={timezone} />
      ) : (
        <div className={view === "week" ? "calendar-grid week" : "calendar-grid day"}>
          {days.map((day) => (
            <div key={day} className={day === today ? "calendar-day today" : "calendar-day"}>
              <h2>{formatDayInZone(`${day}T12:00:00Z`, "UTC")}</h2>
              {(byDay.get(day) ?? []).length === 0 ? (
                <p className="calendar-empty">Nothing scheduled.</p>
              ) : (
                <ul className="calendar-items">
                  {(byDay.get(day) ?? []).map((appointment) => (
                    <AppointmentCard
                      key={appointment.id}
                      appointment={appointment}
                      timezone={timezone}
                    />
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function AgendaView({
  appointments,
  timezone,
}: {
  appointments: Appointment[];
  timezone: string;
}) {
  const sorted = [...appointments].sort((a, b) => a.start_at.localeCompare(b.start_at));
  if (sorted.length === 0) return <p>Nothing scheduled in this period.</p>;
  return (
    <ul className="calendar-agenda">
      {sorted.map((appointment) => (
        <AppointmentCard key={appointment.id} appointment={appointment} timezone={timezone} agenda />
      ))}
    </ul>
  );
}

function AppointmentCard({
  appointment,
  timezone,
  agenda = false,
}: {
  appointment: Appointment;
  timezone: string;
  agenda?: boolean;
}) {
  return (
    <li className={`calendar-item status-${appointment.status}`}>
      <span className="calendar-time">
        {agenda && `${formatDayInZone(appointment.start_at, timezone)} · `}
        {formatTimeInZone(appointment.start_at, timezone)}–
        {formatTimeInZone(appointment.end_at, timezone)}
      </span>{" "}
      <Link href={`/leads/${appointment.lead_id}`}>
        {appointment.lead_name ?? "Lead"}
      </Link>
      <span className="calendar-subject"> · {appointment.subject}</span>
      {appointment.status !== "scheduled" && (
        <span className="calendar-status">
          {" "}
          · {APPOINTMENT_STATUS_LABELS[appointment.status] ?? appointment.status}
        </span>
      )}
      {appointment.assignee_email !== null && (
        <span className="calendar-who"> · {appointment.assignee_email}</span>
      )}
    </li>
  );
}
