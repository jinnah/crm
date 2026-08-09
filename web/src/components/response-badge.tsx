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
      <span className={`badge ${met === false ? "badge-amber" : "badge-green"}`}>
        Responded{seconds !== null ? ` in ${formatDuration(seconds)}` : ""}
        {met === false ? " (late)" : ""}
      </span>
    );
  }
  if (lead.response_overdue) {
    return <span className="badge badge-red">Response overdue</span>;
  }
  return <span className="badge badge-blue">Awaiting first response</span>;
}
