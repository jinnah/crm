import type { Lead } from "@/lib/api";

export function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export function sourceLabel(source: string): string {
  if (source === "voice_call") return "voice call";
  return source.replace(/_/g, " ");
}

const STATUS_BADGE_TONES: Record<string, string> = {
  new: "badge-blue",
  contacted: "badge-teal",
  qualified: "badge-amber",
  won: "badge-green",
  lost: "",
};

export function LeadBadges({
  lead,
  showStatus = true,
}: {
  lead: Lead;
  /** Off when a dedicated status column already shows it. */
  showStatus?: boolean;
}) {
  return (
    <span className="lead-badges">
      {showStatus && (
        <span className={`badge ${STATUS_BADGE_TONES[lead.status] ?? ""}`.trim()}>
          {statusLabel(lead.status)}
        </span>
      )}
      {lead.needs_review && <span className="badge badge-amber">Needs review</span>}
      {lead.archived_at !== null && <span className="badge">Archived</span>}
    </span>
  );
}
