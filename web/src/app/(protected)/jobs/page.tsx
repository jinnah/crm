"use client";

import { Briefcase, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth-context";
import { JobStatusBadge } from "@/components/job-badges";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  InlineError,
  PageHeader,
} from "@/components/ui";
import {
  api,
  errorDetail,
  JOB_STATUS_LABELS,
  JOB_STATUSES,
  type AssignableUser,
  type JobList,
} from "@/lib/api";
import { formatDayInZone } from "@/lib/datetime";

const PAGE_SIZE = 25;

export default function JobsPage() {
  const { user } = useAuth();
  const canManage = user.role === "owner" || user.role === "manager";

  const [data, setData] = useState<JobList | null>(null);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [archived, setArchived] = useState(false);
  const [page, setPage] = useState(1);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (query) params.set("query", query);
    if (status) params.set("status", status);
    if (assignee) params.set("assignee_id", assignee);
    if (archived) params.set("archived", "true");
    void api<JobList>(`/jobs?${params.toString()}`).then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load jobs."));
        return;
      }
      setError(null);
      setData(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [query, status, assignee, archived, page]);

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

  const totalPages = data === null ? 1 : Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <section>
      <PageHeader
        title="Jobs"
        description="Every piece of work, from first quote to final receipt."
      />

      {error !== null && <InlineError>{error}</InlineError>}

      <div className="toolbar">
        <div className="form-field" style={{ flex: "1 1 14rem" }}>
          <label htmlFor="job-search">Search</label>
          <input
            id="job-search"
            type="search"
            placeholder="Job number, customer, address, title or service"
            value={query}
            onChange={(event) => {
              setPage(1);
              setQuery(event.target.value);
            }}
          />
        </div>
        <div className="form-field">
          <label htmlFor="job-status-filter">Status</label>
          <select
            id="job-status-filter"
            value={status}
            onChange={(event) => {
              setPage(1);
              setStatus(event.target.value);
            }}
          >
            <option value="">All</option>
            {JOB_STATUSES.map((value) => (
              <option key={value} value={value}>
                {JOB_STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
        {canManage && (
          <div className="form-field">
            <label htmlFor="job-assignee-filter">Assignee</label>
            <select
              id="job-assignee-filter"
              value={assignee}
              onChange={(event) => {
                setPage(1);
                setAssignee(event.target.value);
              }}
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
        <div className="form-field form-field-checkbox">
          <label htmlFor="job-archived-filter">
            <input
              id="job-archived-filter"
              type="checkbox"
              checked={archived}
              onChange={(event) => {
                setPage(1);
                setArchived(event.target.checked);
              }}
            />{" "}
            Archived
          </label>
        </div>
      </div>

      {data === null ? (
        <p className="page-status" role="status">
          Loading jobs…
        </p>
      ) : data.items.length === 0 ? (
        <Card flush>
          <EmptyState
            icon={<Briefcase size={40} aria-hidden="true" />}
            title={archived ? "No archived jobs" : "No jobs match these filters"}
            description="Create a job from a customer's page to start quoting and invoicing their work."
          />
        </Card>
      ) : (
        <>
          <Card flush className="table-card">
            <table className="data-table">
              <caption className="visually-hidden">Jobs</caption>
              <thead>
                <tr>
                  <th scope="col">Job</th>
                  <th scope="col">Customer</th>
                  <th scope="col">Status</th>
                  <th scope="col">Assignee</th>
                  <th scope="col">Scheduled</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <Link href={`/jobs/${job.id}`} style={{ fontWeight: 600 }}>
                        {job.job_number}
                      </Link>
                      <div className="cell-secondary">
                        {job.title || job.service_type || "—"}
                      </div>
                      {job.service_address !== "" && (
                        <div className="cell-secondary">{job.service_address}</div>
                      )}
                    </td>
                    <td>
                      <Link href={`/leads/${job.lead_id}`}>{job.lead_name ?? "Customer"}</Link>
                    </td>
                    <td>
                      <JobStatusBadge status={job.status} />
                      {job.archived_at !== null && <Badge>Archived</Badge>}
                    </td>
                    <td>{job.assignee_name ?? "—"}</td>
                    <td>
                      {job.scheduled_for !== null
                        ? formatDayInZone(job.scheduled_for, "UTC")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <ul className="record-cards" aria-label="Jobs">
            {data.items.map((job) => (
              <li key={job.id} className="record-card">
                <span className="record-card-title">
                  <Link href={`/jobs/${job.id}`}>{job.job_number}</Link>{" "}
                  <JobStatusBadge status={job.status} />
                </span>
                <span className="record-card-meta">
                  {job.lead_name ?? "Customer"}
                  {job.title !== "" && ` · ${job.title}`}
                </span>
                {job.service_address !== "" && (
                  <span className="record-card-meta">{job.service_address}</span>
                )}
              </li>
            ))}
          </ul>

          {data.total > PAGE_SIZE && (
            <nav className="pagination" aria-label="Job pages">
              <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <span>
                Page {page} of {totalPages} · {data.total} jobs
              </span>
              <Button size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </nav>
          )}
        </>
      )}

      <p className="form-help" style={{ marginTop: "1rem" }}>
        <Plus size={14} aria-hidden="true" style={{ verticalAlign: "-2px" }} /> New jobs are
        created from the customer&apos;s page, so every job starts attached to the right person.
      </p>
    </section>
  );
}
