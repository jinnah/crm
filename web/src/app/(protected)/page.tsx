"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth-context";
import { LeadBadges } from "@/components/lead-badges";
import { ResponseBadge } from "@/components/response-badge";
import {
  api,
  errorDetail,
  type AttentionAppointment,
  type AttentionQueue,
  type Lead,
} from "@/lib/api";
import { formatDateTime, formatInZone } from "@/lib/datetime";

type LeadSectionKey =
  | "unresponded"
  | "overdue"
  | "due_today"
  | "unassigned"
  | "needs_review";

const SECTIONS: Array<{ key: LeadSectionKey; title: string }> = [
  { key: "unresponded", title: "Response overdue" },
  { key: "overdue", title: "Overdue follow-ups" },
  { key: "due_today", title: "Follow-ups due today" },
  { key: "unassigned", title: "New unassigned leads" },
  { key: "needs_review", title: "Needs review" },
];

type AppointmentSectionKey =
  | "appointments_overdue"
  | "appointments_upcoming"
  | "appointment_messages_failed"
  | "appointment_messages_unknown";

const APPOINTMENT_SECTIONS: Array<{
  key: AppointmentSectionKey;
  title: string;
  help?: string;
}> = [
  {
    key: "appointments_overdue",
    title: "Appointments needing an outcome",
    help: "These have finished but are still marked scheduled. Complete them or mark a no-show.",
  },
  { key: "appointments_upcoming", title: "Coming up" },
  {
    key: "appointment_messages_failed",
    title: "Appointment messages that failed",
    help: "The customer was not told. Contact them another way.",
  },
  {
    key: "appointment_messages_unknown",
    title: "Appointment messages never confirmed",
    help: "These may or may not have arrived. Check before sending them again.",
  },
];

export default function HomePage() {
  const { user } = useAuth();
  const [queue, setQueue] = useState<AttentionQueue | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api<AttentionQueue>("/leads/attention").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load the attention queue."));
        return;
      }
      setQueue(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error !== null) {
    return (
      <section>
        <h1>Attention queue</h1>
        <p className="form-error" role="alert">
          {error}
        </p>
      </section>
    );
  }
  if (queue === null) {
    return (
      <p className="page-status" role="status">
        Loading attention queue…
      </p>
    );
  }

  const visibleSections = SECTIONS.filter(
    ({ key }) => !(key === "unassigned" && user.role === "team_member"),
  );
  const empty =
    visibleSections.every(({ key }) => queue[key].length === 0) &&
    APPOINTMENT_SECTIONS.every(({ key }) => (queue[key] ?? []).length === 0);

  return (
    <section>
      <h1>Attention queue</h1>
      {empty ? (
        <p>Nothing needs attention right now.</p>
      ) : (
        <>
          {visibleSections.map(({ key, title }) =>
            queue[key].length === 0 ? null : (
              <div key={key} className="attention-group">
                <h2>{title}</h2>
                <ul className="attention-list">
                  {queue[key].map((lead) => (
                    <AttentionRow key={`${key}-${lead.id}`} lead={lead} />
                  ))}
                </ul>
              </div>
            ),
          )}
          {APPOINTMENT_SECTIONS.map(({ key, title, help }) =>
            (queue[key] ?? []).length === 0 ? null : (
              <div key={key} className="attention-group">
                <h2>{title}</h2>
                {help !== undefined && <p className="form-help">{help}</p>}
                <ul className="attention-list">
                  {(queue[key] ?? []).map((appointment) => (
                    <AppointmentRow
                      key={`${key}-${appointment.id}`}
                      appointment={appointment}
                    />
                  ))}
                </ul>
              </div>
            ),
          )}
        </>
      )}
    </section>
  );
}

function AppointmentRow({ appointment }: { appointment: AttentionAppointment }) {
  return (
    <li>
      <Link href={`/leads/${appointment.lead_id}`}>{appointment.lead_name ?? "Lead"}</Link>{" "}
      <span className="attention-when">
        · {appointment.subject} · {formatInZone(appointment.start_at, appointment.timezone)}
      </span>
      {appointment.detail !== null && <span className="attention-who"> · {appointment.detail}</span>}
    </li>
  );
}

function AttentionRow({ lead }: { lead: Lead }) {
  return (
    <li>
      <Link href={`/leads/${lead.id}`}>{lead.name || lead.email || lead.phone || "Unnamed lead"}</Link>{" "}
      <LeadBadges lead={lead} /> <ResponseBadge lead={lead} />
      {lead.next_follow_up_at !== null && (
        <span className="attention-when"> · follow-up {formatDateTime(lead.next_follow_up_at)}</span>
      )}
      {lead.assignee_email !== null && (
        <span className="attention-who"> · {lead.assignee_email}</span>
      )}
    </li>
  );
}
