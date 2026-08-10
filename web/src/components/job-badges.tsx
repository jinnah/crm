import { Badge } from "@/components/ui";
import { JOB_STATUS_LABELS, type JobStatus } from "@/lib/api";

const TONES: Record<JobStatus, "gray" | "blue" | "teal" | "green" | "amber" | "red"> = {
  new: "gray",
  quoted: "blue",
  approved: "teal",
  scheduled: "blue",
  in_progress: "amber",
  completed: "green",
  canceled: "gray",
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return <Badge tone={TONES[status]}>{JOB_STATUS_LABELS[status]}</Badge>;
}

const COMMERCIAL_TONES: Record<string, "gray" | "blue" | "teal" | "green" | "amber" | "red"> = {
  draft: "gray",
  sent: "blue",
  viewed: "teal",
  accepted: "green",
  declined: "red",
  expired: "amber",
  voided: "gray",
  partially_paid: "amber",
  paid: "green",
  overdue: "red",
  issued: "green",
};

const COMMERCIAL_LABELS: Record<string, string> = {
  draft: "Draft",
  sent: "Sent",
  viewed: "Viewed",
  accepted: "Accepted",
  declined: "Declined",
  expired: "Expired",
  voided: "Voided",
  partially_paid: "Partially paid",
  paid: "Paid",
  overdue: "Overdue",
  issued: "Issued",
};

export function CommercialStatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={COMMERCIAL_TONES[status] ?? "gray"}>
      {COMMERCIAL_LABELS[status] ?? status}
    </Badge>
  );
}

const EMAIL_TONES: Record<string, "gray" | "blue" | "teal" | "green" | "amber" | "red"> = {
  pending: "gray",
  claimed: "blue",
  submitted: "teal",
  delivered: "green",
  failed: "red",
  unknown: "amber",
  suppressed: "gray",
};

export function EmailStatusBadge({ status }: { status: string }) {
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  return <Badge tone={EMAIL_TONES[status] ?? "gray"}>{label}</Badge>;
}
