"use client";

import { CalendarClock, Inbox, MessageSquareWarning, Sun } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "@/components/auth-context";
import { LeadBadges } from "@/components/lead-badges";
import { ResponseBadge } from "@/components/response-badge";
import { Card, EmptyState, InlineError } from "@/components/ui";
import {
  api,
  errorDetail,
  type AttentionAppointment,
  type AttentionQueue,
  type Lead,
} from "@/lib/api";
import { formatDateTime, formatInZone } from "@/lib/datetime";

/** Rows shown per card before pointing at the full workspace. */
const ROW_CAP = 6;

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
        <h1>Today</h1>
        <InlineError>{error}</InlineError>
      </section>
    );
  }
  if (queue === null) {
    return (
      <p className="page-status" role="status">
        Loading your day…
      </p>
    );
  }

  const showUnassigned = user.role !== "team_member";

  // Follow-ups: overdue and due-today are one job with two urgencies.
  const followUps = [...queue.overdue, ...queue.due_today];
  const unassigned = showUnassigned ? queue.unassigned : [];
  const messageIssues = [
    ...queue.appointment_messages_failed.map((item) => ({ item, failed: true })),
    ...queue.appointment_messages_unknown.map((item) => ({ item, failed: false })),
  ];

  const empty =
    queue.unresponded.length === 0 &&
    followUps.length === 0 &&
    unassigned.length === 0 &&
    queue.needs_review.length === 0 &&
    queue.appointments_overdue.length === 0 &&
    queue.appointments_upcoming.length === 0 &&
    messageIssues.length === 0;

  return (
    <section>
      <header className="page-header">
        <div className="page-header-text">
          <h1>Today</h1>
          <p>Everything that needs a person, in one place.</p>
        </div>
      </header>

      <div className="summary-grid">
        <SummaryCard
          tone="red"
          count={queue.unresponded.length}
          label="Response overdue"
          hint="New requests past the response target"
        />
        <SummaryCard
          tone="amber"
          count={followUps.length}
          label="Follow-ups due"
          hint="Promised call-backs to make"
        />
        {showUnassigned && (
          <SummaryCard
            tone="blue"
            count={unassigned.length}
            label="Unassigned leads"
            hint="Waiting for an owner"
          />
        )}
        <SummaryCard
          tone="teal"
          count={queue.appointments_upcoming.length}
          label="Upcoming appointments"
          hint="Within the configured window"
        />
      </div>

      {empty ? (
        <Card flush>
          <EmptyState
            icon={<Sun size={40} aria-hidden="true" />}
            title="All clear"
            description="No overdue responses, follow-ups, unassigned leads or appointment problems right now."
            action={
              <Link className="button-link" href="/leads">
                Go to leads
              </Link>
            }
          />
        </Card>
      ) : (
        <div className="attention-grid">
          {queue.unresponded.length > 0 && (
            <AttentionCard
              title="Response overdue"
              count={queue.unresponded.length}
              description="These people asked for help and have not heard back. Reply or mark them contacted."
            >
              {queue.unresponded.slice(0, ROW_CAP).map((lead) => (
                <LeadRow key={lead.id} lead={lead} />
              ))}
            </AttentionCard>
          )}

          {followUps.length > 0 && (
            <AttentionCard
              title="Follow-ups due"
              count={followUps.length}
              description="Call-backs you promised, most overdue first."
            >
              {followUps.slice(0, ROW_CAP).map((lead) => (
                <LeadRow key={lead.id} lead={lead}>
                  {lead.next_follow_up_at !== null && (
                    <span className="attention-when">
                      {relativeDue(lead.next_follow_up_at)} · {formatDateTime(lead.next_follow_up_at)}
                    </span>
                  )}
                  {lead.assignee_email !== null && (
                    <span className="attention-who">{lead.assignee_email}</span>
                  )}
                </LeadRow>
              ))}
            </AttentionCard>
          )}

          {unassigned.length > 0 && (
            <AttentionCard
              title="New unassigned leads"
              count={unassigned.length}
              description="Give each one an owner so nothing sits."
              icon={<Inbox size={16} aria-hidden="true" />}
            >
              {unassigned.slice(0, ROW_CAP).map((lead) => (
                <LeadRow key={lead.id} lead={lead} />
              ))}
            </AttentionCard>
          )}

          {queue.needs_review.length > 0 && (
            <AttentionCard
              title="Needs review"
              count={queue.needs_review.length}
              description="Captured automatically with details worth checking."
            >
              {queue.needs_review.slice(0, ROW_CAP).map((lead) => (
                <LeadRow key={lead.id} lead={lead} />
              ))}
            </AttentionCard>
          )}

          {queue.appointments_overdue.length > 0 && (
            <AttentionCard
              title="Appointments needing an outcome"
              count={queue.appointments_overdue.length}
              description="These finished but are still marked scheduled. Complete them or record a no-show."
              icon={<CalendarClock size={16} aria-hidden="true" />}
            >
              {queue.appointments_overdue.slice(0, ROW_CAP).map((appointment) => (
                <AppointmentRow key={appointment.id} appointment={appointment} />
              ))}
            </AttentionCard>
          )}

          {queue.appointments_upcoming.length > 0 && (
            <AttentionCard
              title="Coming up"
              count={queue.appointments_upcoming.length}
              description="The next visits on the calendar."
              icon={<CalendarClock size={16} aria-hidden="true" />}
              action={
                <Link className="btn btn-tertiary" href="/calendar">
                  Open calendar
                </Link>
              }
            >
              {queue.appointments_upcoming.slice(0, ROW_CAP).map((appointment) => (
                <AppointmentRow key={appointment.id} appointment={appointment} />
              ))}
            </AttentionCard>
          )}

          {messageIssues.length > 0 && (
            <AttentionCard
              title="Appointment messages to check"
              count={messageIssues.length}
              description="Failed messages never arrived — contact the customer another way. Unconfirmed ones may have arrived; check before resending."
              icon={<MessageSquareWarning size={16} aria-hidden="true" />}
            >
              {messageIssues.slice(0, ROW_CAP).map(({ item, failed }) => (
                <AppointmentRow key={`${failed}-${item.id}`} appointment={item}>
                  <span className={failed ? "badge badge-red" : "badge badge-amber"}>
                    {failed ? "Failed" : "Unconfirmed"}
                  </span>
                </AppointmentRow>
              ))}
            </AttentionCard>
          )}
        </div>
      )}
    </section>
  );
}

function SummaryCard({
  tone,
  count,
  label,
  hint,
}: {
  tone: "red" | "amber" | "blue" | "teal";
  count: number;
  label: string;
  hint: string;
}) {
  return (
    <div className={`summary-card summary-card-${tone}`}>
      <span className="summary-count">{count}</span>
      <span className="summary-label">{label}</span>
      <span className="summary-hint">{hint}</span>
    </div>
  );
}

function AttentionCard({
  title,
  count,
  description,
  icon,
  action,
  children,
}: {
  title: string;
  count: number;
  description: string;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card
      flush
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
          {icon}
          {title} <span className="badge">{count}</span>
        </span>
      }
      description={description}
      actions={action}
    >
      <ul className="attention-list">
        {children}
        {count > ROW_CAP && (
          <li>
            <Link href="/leads">Show all {count} in Leads</Link>
          </li>
        )}
      </ul>
    </Card>
  );
}

function LeadRow({ lead, children }: { lead: Lead; children?: ReactNode }) {
  return (
    <li>
      <Link href={`/leads/${lead.id}`}>{lead.name || lead.email || lead.phone || "Unnamed lead"}</Link>
      <LeadBadges lead={lead} />
      <ResponseBadge lead={lead} />
      {children}
    </li>
  );
}

function AppointmentRow({
  appointment,
  children,
}: {
  appointment: AttentionAppointment;
  children?: ReactNode;
}) {
  return (
    <li>
      <Link href={`/leads/${appointment.lead_id}`}>{appointment.lead_name ?? "Lead"}</Link>
      <span className="attention-when">
        {appointment.subject} · {formatInZone(appointment.start_at, appointment.timezone)}
      </span>
      {children}
      {appointment.detail !== null && children === undefined && (
        <span className="attention-note">{appointment.detail}</span>
      )}
    </li>
  );
}

/** "Due today" / "Overdue" alongside the exact time. */
function relativeDue(iso: string): string {
  const due = new Date(iso);
  if (Number.isNaN(due.getTime())) return "";
  const now = new Date();
  if (due.getTime() < now.getTime()) return "Overdue";
  return due.toDateString() === now.toDateString() ? "Due today" : "Upcoming";
}
