import type { Lead } from "@/lib/api";

export function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export function sourceLabel(source: string): string {
  return source.replace(/_/g, " ");
}

export function LeadBadges({ lead }: { lead: Lead }) {
  return (
    <span className="lead-badges">
      <span className={`badge badge-status-${lead.status}`}>{statusLabel(lead.status)}</span>
      {lead.needs_review && <span className="badge badge-review">Needs review</span>}
      {lead.archived_at !== null && <span className="badge badge-archived">Archived</span>}
    </span>
  );
}
