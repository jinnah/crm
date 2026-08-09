import type { Lead } from "@/lib/api";

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/** First-response state: answered (with time), overdue, or awaiting reply. */
export function ResponseBadge({ lead }: { lead: Lead }) {
  if (lead.first_inbound_at === null) return null;
  if (lead.first_response_at !== null) {
    const seconds = lead.first_response_seconds;
    const met = lead.response_target_met;
    return (
      <span className={`response-badge${met === false ? " response-late" : ""}`}>
        Responded{seconds !== null ? ` in ${formatDuration(seconds)}` : ""}
        {met === false ? " (late)" : ""}
      </span>
    );
  }
  if (lead.response_overdue) {
    return <span className="response-badge response-late">Response overdue</span>;
  }
  return <span className="response-badge">Awaiting first response</span>;
}
