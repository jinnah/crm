"""Minimal RFC 5545 calendar output for a single appointment.

Only what a calendar needs: when, what it is called, and whether it stands.
Never notes, phone numbers, email addresses or CRM credentials.
"""

from app.models import Appointment, CommunicationSettings

CRLF = "\r\n"
MAX_LINE = 75


def escape_text(value: str) -> str:
    """RFC 5545 TEXT escaping: backslash, semicolon, comma and newlines."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold(line: str) -> str:
    """Fold long content lines onto continuation lines (leading space)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= MAX_LINE:
        return line
    pieces: list[str] = []
    current = b""
    for char in line:
        chunk = char.encode("utf-8")
        limit = MAX_LINE if not pieces else MAX_LINE - 1
        if len(current) + len(chunk) > limit:
            pieces.append(current.decode("utf-8"))
            current = b""
        current += chunk
    if current:
        pieces.append(current.decode("utf-8"))
    return (CRLF + " ").join(pieces)


def _stamp(moment) -> str:
    return moment.astimezone(__import__("datetime").UTC).strftime("%Y%m%dT%H%M%SZ")


def build_ics(appointment: Appointment, settings_row: CommunicationSettings) -> str:
    """A single-event VCALENDAR for this appointment."""
    status = "CANCELLED" if appointment.status == "canceled" else "CONFIRMED"
    description = f"{appointment.subject} with {settings_row.business_name}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Service CRM//Appointments//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        # Stable across regenerations so calendars update rather than duplicate.
        f"UID:appointment-{appointment.id}@service-crm",
        f"DTSTAMP:{_stamp(appointment.updated_at or appointment.created_at)}",
        f"DTSTART:{_stamp(appointment.start_at)}",
        f"DTEND:{_stamp(appointment.end_at)}",
        f"SUMMARY:{escape_text(appointment.subject)}",
        f"DESCRIPTION:{escape_text(description)}",
        f"ORGANIZER;CN={escape_text(settings_row.business_name)}:MAILTO:noreply@invalid",
        f"STATUS:{status}",
        f"SEQUENCE:{0 if appointment.status != 'canceled' else 1}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return CRLF.join(fold(line) for line in lines) + CRLF
