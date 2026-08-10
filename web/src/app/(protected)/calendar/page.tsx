"use client";

import {
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  Globe,
  Phone,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-context";
import { Button, Card, EmptyState, InlineError, PageHeader } from "@/components/ui";
import {
  api,
  errorDetail,
  APPOINTMENT_STATUS_LABELS,
  WEEKDAY_KEYS,
  type Appointment,
  type AssignableUser,
  type SchedulingBasics,
} from "@/lib/api";
import {
  addDays,
  dayKeyInZone,
  formatDayInZone,
  formatTimeInZone,
  minutesInZone,
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

const HOUR_HEIGHT_REM = 3.5;

/** Accessible-name suffix per origin; staff-created is the unmarked default. */
const ORIGIN_LABELS: Record<Appointment["origin"], string> = {
  staff: "",
  customer: ", booked online",
  voice: ", booked by phone",
};

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
  // Phones get the readable list first; wide screens get the week grid.
  const [view, setView] = useState<View>(() =>
    typeof window !== "undefined" && window.innerWidth < 768 ? "agenda" : "week",
  );
  const [anchor, setAnchor] = useState<string | null>(null);
  const [staffFilter, setStaffFilter] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  // Re-rendered each minute so the current-time line tracks the clock.
  const [nowIso, setNowIso] = useState(() => new Date().toISOString());

  useEffect(() => {
    const timer = window.setInterval(() => setNowIso(new Date().toISOString()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

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

  // The visible hours cover the business day with an hour's margin, and
  // stretch further only when an appointment actually falls outside them.
  const [gridStartHour, gridEndHour] = useMemo(() => {
    let earliest = 8;
    let latest = 18;
    const hours = basics?.business_hours;
    if (hours) {
      const opens: number[] = [];
      const closes: number[] = [];
      for (const key of WEEKDAY_KEYS) {
        for (const window of hours[key] ?? []) {
          opens.push(Number(window[0].slice(0, 2)));
          closes.push(Number(window[1].slice(0, 2)) + (window[1].slice(3) === "00" ? 0 : 1));
        }
      }
      if (opens.length > 0) {
        earliest = Math.min(...opens);
        latest = Math.max(...closes);
      }
    }
    for (const appointment of appointments ?? []) {
      if (appointment.status === "canceled") continue;
      earliest = Math.min(earliest, Math.floor(minutesInZone(appointment.start_at, timezone) / 60));
      latest = Math.max(latest, Math.ceil(minutesInZone(appointment.end_at, timezone) / 60) || 24);
    }
    return [Math.max(0, earliest - 1), Math.min(24, latest + 1)];
  }, [basics, appointments, timezone]);

  if (anchor === null || rangeStart === null) {
    return error !== null ? (
      <section>
        <h1>Calendar</h1>
        <InlineError>{error}</InlineError>
      </section>
    ) : (
      <p className="page-status" role="status">
        Loading calendar…
      </p>
    );
  }

  const step = view === "week" ? 7 : view === "agenda" ? 14 : 1;
  const today = dayKeyInZone(nowIso, timezone);
  const rangeLabel =
    view === "day"
      ? formatDayInZone(`${rangeStart}T12:00:00Z`, "UTC")
      : `${formatDayInZone(`${rangeStart}T12:00:00Z`, "UTC")} – ${formatDayInZone(
          `${addDays(rangeStart, VIEW_SPAN[view] - 1)}T12:00:00Z`,
          "UTC",
        )}`;

  return (
    <section className="calendar-page">
      <PageHeader title="Calendar" />
      <div className="calendar-toolbar">
        <div className="segmented" role="group" aria-label="Calendar view">
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
        <div className="calendar-group" role="group" aria-label="Date navigation">
          <Button
            size="sm"
            aria-label="Previous"
            onClick={() => setAnchor((day) => addDays(day ?? today, -step))}
          >
            <ChevronLeft size={16} aria-hidden="true" />
          </Button>
          <Button size="sm" onClick={() => setAnchor(today)}>
            Today
          </Button>
          <Button
            size="sm"
            aria-label="Next"
            onClick={() => setAnchor((day) => addDays(day ?? today, step))}
          >
            <ChevronRight size={16} aria-hidden="true" />
          </Button>
          <span className="calendar-range">{rangeLabel}</span>
        </div>
        {canManage && (
          <div className="form-field calendar-staff">
            <label htmlFor="calendar-staff">Staff</label>
            <select
              id="calendar-staff"
              value={staffFilter}
              onChange={(event) => setStaffFilter(event.target.value)}
            >
              <option value="">Everyone</option>
              {users.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.display_name || option.email}
                </option>
              ))}
            </select>
          </div>
        )}
        <span className="calendar-zone">Times are shown in {timezone}.</span>
      </div>

      {error !== null && <InlineError>{error}</InlineError>}

      {appointments === null ? (
        <p className="page-status" role="status">
          Loading appointments…
        </p>
      ) : view === "agenda" ? (
        <AgendaView days={days} byDay={byDay} timezone={timezone} today={today} />
      ) : (
        <TimeGrid
          days={days}
          byDay={byDay}
          timezone={timezone}
          today={today}
          nowMinutes={minutesInZone(nowIso, timezone)}
          businessHours={basics?.business_hours ?? null}
          gridStartHour={gridStartHour}
          gridEndHour={gridEndHour}
        />
      )}
    </section>
  );
}

/* ----------------------------------------------------------------------- */
/* Agenda                                                                   */
/* ----------------------------------------------------------------------- */

function AgendaView({
  days,
  byDay,
  timezone,
  today,
}: {
  days: string[];
  byDay: Map<string, Appointment[]>;
  timezone: string;
  today: string;
}) {
  const daysWithItems = days.filter((day) => (byDay.get(day) ?? []).length > 0);
  if (daysWithItems.length === 0) {
    return (
      <Card flush>
        <EmptyState
          icon={<CalendarDays size={40} aria-hidden="true" />}
          title="Nothing scheduled in this period"
          description="Appointments booked by staff or customers appear here as soon as they exist."
        />
      </Card>
    );
  }
  return (
    <Card flush>
      {daysWithItems.map((day) => (
        <div key={day} className="agenda-day">
          <h2 className="agenda-day-header">
            {formatDayInZone(`${day}T12:00:00Z`, "UTC")}
            {day === today && " · Today"}
          </h2>
          <ul className="agenda-items">
            {(byDay.get(day) ?? []).map((appointment) => (
              <li key={appointment.id}>
                <span className="agenda-time">
                  {formatTimeInZone(appointment.start_at, timezone)}–
                  {formatTimeInZone(appointment.end_at, timezone)}
                </span>
                <Link href={`/leads/${appointment.lead_id}`} style={{ fontWeight: 600 }}>
                  {appointment.lead_name ?? "Lead"}
                </Link>
                <span className="agenda-detail">{appointment.subject}</span>
                {appointment.status !== "scheduled" && (
                  <StatusBadge status={appointment.status} />
                )}
                {appointment.origin !== "staff" && (
                  <span className="badge">
                    {appointment.origin === "voice" ? "Phone call" : "Booked online"}
                  </span>
                )}
                {appointment.assignee_name !== null && (
                  <span className="agenda-detail">{appointment.assignee_name}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Card>
  );
}

function StatusBadge({ status }: { status: Appointment["status"] }) {
  const tone = status === "completed" ? "badge-green" : status === "no_show" ? "badge-red" : "";
  return (
    <span className={`badge ${tone}`.trim()}>
      {APPOINTMENT_STATUS_LABELS[status] ?? status}
    </span>
  );
}

/* ----------------------------------------------------------------------- */
/* Time grid (day and week)                                                 */
/* ----------------------------------------------------------------------- */

type Positioned = {
  appointment: Appointment;
  top: number;
  height: number;
  lane: number;
  lanes: number;
};

/** Assign overlapping appointments to side-by-side lanes within one day. */
function layoutDay(
  items: Appointment[],
  timezone: string,
  gridStartMin: number,
  gridEndMin: number,
): Positioned[] {
  type Interval = { appointment: Appointment; start: number; end: number; lane: number };
  const intervals: Interval[] = items.map((appointment) => {
    const start = Math.max(minutesInZone(appointment.start_at, timezone), gridStartMin);
    const rawEnd = minutesInZone(appointment.end_at, timezone);
    // An appointment running past midnight renders to the bottom of its day.
    const end = Math.min(rawEnd > start ? rawEnd : gridEndMin, gridEndMin);
    return { appointment, start, end: Math.max(end, start + 20), lane: 0 };
  });
  intervals.sort((a, b) => a.start - b.start || b.end - a.end);

  // Greedy lane assignment inside clusters of transitively overlapping items.
  const result: Positioned[] = [];
  let cluster: Interval[] = [];
  let clusterEnd = -1;

  function flush() {
    const lanes = Math.max(1, ...cluster.map((item) => item.lane + 1));
    for (const item of cluster) {
      result.push({
        appointment: item.appointment,
        top: item.start - gridStartMin,
        height: item.end - item.start,
        lane: item.lane,
        lanes,
      });
    }
    cluster = [];
  }

  for (const interval of intervals) {
    if (cluster.length > 0 && interval.start >= clusterEnd) flush();
    const taken = new Set(
      cluster
        .filter((item) => item.end > interval.start)
        .map((item) => item.lane),
    );
    let lane = 0;
    while (taken.has(lane)) lane += 1;
    interval.lane = lane;
    cluster.push(interval);
    clusterEnd = Math.max(clusterEnd, interval.end);
  }
  if (cluster.length > 0) flush();
  return result;
}

function TimeGrid({
  days,
  byDay,
  timezone,
  today,
  nowMinutes,
  businessHours,
  gridStartHour,
  gridEndHour,
}: {
  days: string[];
  byDay: Map<string, Appointment[]>;
  timezone: string;
  today: string;
  nowMinutes: number;
  businessHours: Record<string, string[][]> | null;
  gridStartHour: number;
  gridEndHour: number;
}) {
  const gridStartMin = gridStartHour * 60;
  const gridEndMin = gridEndHour * 60;
  const hourCount = gridEndHour - gridStartHour;
  const totalHeight = hourCount * HOUR_HEIGHT_REM;

  const toRem = (minutes: number) => (minutes / 60) * HOUR_HEIGHT_REM;

  return (
    <Card flush className="time-grid-card">
      <div
        className="time-grid"
        role="grid"
        aria-label="Appointments"
        style={{
          gridTemplateColumns: `3.5rem repeat(${days.length}, minmax(${
            days.length > 1 ? "7.5rem" : "14rem"
          }, 1fr))`,
        }}
      >
        <div className="time-grid-head">
          <div aria-hidden="true" style={{ borderLeft: "none" }} />
          {days.map((day) => (
            <div key={day} className={day === today ? "today" : undefined}>
              {shortDayLabel(day)}
              {day === today && " · Today"}
            </div>
          ))}
        </div>

        <div className="time-axis" aria-hidden="true" style={{ height: `${totalHeight}rem` }}>
          {Array.from({ length: hourCount }, (_, index) => (
            <div key={index} className="time-axis-label">
              {hourLabel(gridStartHour + index)}
            </div>
          ))}
        </div>

        {days.map((day) => {
          const weekday = WEEKDAY_KEYS[(new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7];
          const windows = businessHours?.[weekday] ?? [];
          const closed = closedSegments(windows, gridStartMin, gridEndMin);
          const positioned = layoutDay(byDay.get(day) ?? [], timezone, gridStartMin, gridEndMin);
          return (
            <div
              key={day}
              className="day-column"
              role="gridcell"
              aria-label={formatDayInZone(`${day}T12:00:00Z`, "UTC")}
              style={{ height: `${totalHeight}rem` }}
            >
              {Array.from({ length: hourCount }, (_, index) => (
                <div
                  key={index}
                  className="hour-line"
                  style={{ top: `${index * HOUR_HEIGHT_REM}rem` }}
                />
              ))}
              {closed.map(([from, to], index) => (
                <div
                  key={`closed-${index}`}
                  className="closed-hours"
                  style={{
                    top: `${toRem(from - gridStartMin)}rem`,
                    height: `${toRem(to - from)}rem`,
                  }}
                />
              ))}
              {day === today && nowMinutes >= gridStartMin && nowMinutes <= gridEndMin && (
                <div
                  className="now-line"
                  style={{ top: `${toRem(nowMinutes - gridStartMin)}rem` }}
                  aria-hidden="true"
                />
              )}
              {positioned.map(({ appointment, top, height, lane, lanes }) => {
                const startMin = minutesInZone(appointment.start_at, timezone);
                const rawEndMin = minutesInZone(appointment.end_at, timezone);
                // A manually created appointment outside working hours gets an
                // explicit exception treatment; booking flows can never make one.
                const outsideHours =
                  appointment.status !== "canceled" &&
                  !withinOpenWindows(windows, startMin, rawEndMin > startMin ? rawEndMin : 24 * 60);
                return (
                  <Link
                    key={appointment.id}
                    href={`/leads/${appointment.lead_id}`}
                    className={`appointment-block status-${appointment.status}${
                      outsideHours ? " outside-hours" : ""
                    }`}
                    style={{
                      top: `${toRem(top)}rem`,
                      height: `${toRem(height)}rem`,
                      left: `calc(${(lane / lanes) * 100}% + 2px)`,
                      width: `calc(${100 / lanes}% - 5px)`,
                    }}
                    aria-label={`${formatTimeInZone(appointment.start_at, timezone)} ${
                      appointment.lead_name ?? "Lead"
                    }: ${appointment.subject}${
                      appointment.status !== "scheduled"
                        ? ` (${APPOINTMENT_STATUS_LABELS[appointment.status]})`
                        : ""
                    }${ORIGIN_LABELS[appointment.origin]}${
                      outsideHours ? ", outside business hours" : ""
                    }`}
                  >
                    <span className="block-time">
                      {formatTimeInZone(appointment.start_at, timezone)}
                      {appointment.origin === "customer" && (
                        <Globe size={11} className="block-icon" aria-hidden="true" />
                      )}
                      {appointment.origin === "voice" && (
                        <Phone size={11} className="block-icon" aria-hidden="true" />
                      )}
                      {appointment.status === "completed" && (
                        <Check size={11} className="block-icon" aria-hidden="true" />
                      )}
                      {appointment.status === "no_show" && (
                        <TriangleAlert size={11} className="block-icon" aria-hidden="true" />
                      )}
                    </span>{" "}
                    <span className="block-title">{appointment.lead_name ?? "Lead"}</span>
                    {height >= 45 && <span className="block-title">{appointment.subject}</span>}
                    {height >= 70 && appointment.assignee_name !== null && lanes === 1 && (
                      <span className="block-title block-meta">{appointment.assignee_name}</span>
                    )}
                  </Link>
                );
              })}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/** The parts of the visible day outside every open window. */
function closedSegments(
  windows: string[][],
  gridStartMin: number,
  gridEndMin: number,
): Array<[number, number]> {
  const toMinutes = (value: string) => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
  const open = windows
    .map(([from, to]) => [toMinutes(from), toMinutes(to)] as [number, number])
    .sort((a, b) => a[0] - b[0]);
  const closed: Array<[number, number]> = [];
  let cursor = gridStartMin;
  for (const [from, to] of open) {
    if (from > cursor) closed.push([cursor, Math.min(from, gridEndMin)]);
    cursor = Math.max(cursor, to);
  }
  if (cursor < gridEndMin) closed.push([cursor, gridEndMin]);
  return closed.filter(([from, to]) => to > from);
}

/** True when the whole span sits inside one open business-hours window. */
function withinOpenWindows(windows: string[][], startMin: number, endMin: number): boolean {
  const toMinutes = (value: string) => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
  return windows.some(([from, to]) => toMinutes(from) <= startMin && endMin <= toMinutes(to));
}

/** Locale-aware hour label — "8 AM" or "08" depending on the user's locale. */
function hourLabel(hour: number): string {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(new Date(2000, 0, 1, hour));
}

function shortDayLabel(day: string): string {
  const date = new Date(`${day}T12:00:00Z`);
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
}
