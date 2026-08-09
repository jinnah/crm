export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "—";
  return value.toLocaleString();
}

/** ISO timestamp → value for <input type="datetime-local"> in local time. */
export function toLocalInputValue(iso: string | null): string {
  if (!iso) return "";
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "";
  const offset = value.getTimezoneOffset();
  return new Date(value.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

/** <input type="datetime-local"> value → ISO timestamp (or null when empty). */
export function fromLocalInputValue(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString();
}

function inZone(iso: string, timeZone: string, options: Intl.DateTimeFormatOptions): string {
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone }).format(value);
  } catch {
    // An unusable zone must never blank out the time it was meant to label.
    return new Intl.DateTimeFormat(undefined, options).format(value);
  }
}

/**
 * An appointment happens at the business's time zone, not the viewer's, so
 * every appointment time is shown in the zone it was scheduled under and the
 * zone is named alongside it.
 */
export function formatInZone(iso: string, timeZone: string): string {
  // Individual components rather than dateStyle/timeStyle: Intl rejects those
  // shorthands when combined with timeZoneName, and the zone must be shown.
  return inZone(iso, timeZone, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

export function formatTimeInZone(iso: string, timeZone: string): string {
  return inZone(iso, timeZone, { hour: "2-digit", minute: "2-digit" });
}

export function formatDayInZone(iso: string, timeZone: string): string {
  return inZone(iso, timeZone, { weekday: "long", day: "numeric", month: "long" });
}

/** The YYYY-MM-DD calendar day an instant falls on inside a time zone. */
export function dayKeyInZone(iso: string, timeZone: string): string {
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(value);
  } catch {
    return value.toISOString().slice(0, 10);
  }
}

/** Shift a YYYY-MM-DD day string by whole days without crossing into local time. */
export function addDays(day: string, delta: number): string {
  const parsed = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return day;
  parsed.setUTCDate(parsed.getUTCDate() + delta);
  return parsed.toISOString().slice(0, 10);
}

/** The Monday of the week containing a YYYY-MM-DD day. */
export function startOfWeek(day: string): string {
  const parsed = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return day;
  const weekday = (parsed.getUTCDay() + 6) % 7; // Monday = 0
  return addDays(day, -weekday);
}
