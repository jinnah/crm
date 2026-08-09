"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { LeadBadges, sourceLabel, statusLabel } from "@/components/lead-badges";
import {
  api,
  errorDetail,
  LEAD_SOURCES,
  LEAD_STATUSES,
  type AssignableUser,
  type LeadList,
} from "@/lib/api";
import { formatDateTime } from "@/lib/datetime";

const PAGE_SIZE = 25;

export default function LeadsPage() {
  const { user } = useAuth();
  const canManage = user.role === "owner" || user.role === "manager";

  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [source, setSource] = useState("");
  const [archived, setArchived] = useState(false);
  const [needsReview, setNeedsReview] = useState(false);
  const [page, setPage] = useState(1);

  const [data, setData] = useState<LeadList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [users, setUsers] = useState<AssignableUser[]>([]);
  const loading = data === null && error === null;

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

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams();
    if (query) params.set("query", query);
    if (status) params.set("status", status);
    if (source) params.set("source", source);
    if (archived) params.set("archived", "true");
    if (needsReview) params.set("needs_review", "true");
    if (assignee === "unassigned") {
      params.set("unassigned", "true");
    } else if (assignee) {
      params.set("assignee_id", assignee);
    }
    params.set("page", String(page));
    params.set("page_size", String(PAGE_SIZE));
    void api<LeadList>(`/leads?${params.toString()}`).then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load leads."));
        return;
      }
      setError(null);
      setData(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [query, status, source, archived, needsReview, assignee, page]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(1);
    setQuery(searchInput.trim());
  }

  const totalPages = data === null ? 1 : Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <section>
      <div className="page-head">
        <h1>Leads</h1>
        {canManage && (
          <Link className="button-link" href="/leads/new">
            New lead
          </Link>
        )}
      </div>

      <form className="lead-filters" onSubmit={submitSearch}>
        <div className="form-field">
          <label htmlFor="lead-search">Search</label>
          <input
            id="lead-search"
            type="search"
            placeholder="Name, email, phone, company"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="filter-status">Status</label>
          <select
            id="filter-status"
            value={status}
            onChange={(event) => {
              setPage(1);
              setStatus(event.target.value);
            }}
          >
            <option value="">All</option>
            {LEAD_STATUSES.map((value) => (
              <option key={value} value={value}>
                {statusLabel(value)}
              </option>
            ))}
          </select>
        </div>
        {canManage && (
          <div className="form-field">
            <label htmlFor="filter-assignee">Assignee</label>
            <select
              id="filter-assignee"
              value={assignee}
              onChange={(event) => {
                setPage(1);
                setAssignee(event.target.value);
              }}
            >
              <option value="">Anyone</option>
              <option value="unassigned">Unassigned</option>
              {users.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.email}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="form-field">
          <label htmlFor="filter-source">Source</label>
          <select
            id="filter-source"
            value={source}
            onChange={(event) => {
              setPage(1);
              setSource(event.target.value);
            }}
          >
            <option value="">All</option>
            {LEAD_SOURCES.map((value) => (
              <option key={value} value={value}>
                {sourceLabel(value)}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field form-field-checkbox">
          <label htmlFor="filter-archived">
            <input
              id="filter-archived"
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
        <div className="form-field form-field-checkbox">
          <label htmlFor="filter-review">
            <input
              id="filter-review"
              type="checkbox"
              checked={needsReview}
              onChange={(event) => {
                setPage(1);
                setNeedsReview(event.target.checked);
              }}
            />{" "}
            Needs review
          </label>
        </div>
        <button type="submit">Search</button>
      </form>

      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading && (
        <p className="page-status" role="status">
          Loading leads…
        </p>
      )}
      {!loading && data !== null && data.items.length === 0 && (
        <p>No leads match these filters.</p>
      )}
      {!loading && data !== null && data.items.length > 0 && (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Contact</th>
                <th scope="col">Company</th>
                <th scope="col">Status</th>
                <th scope="col">Source</th>
                <th scope="col">Assignee</th>
                <th scope="col">Next follow-up</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((lead) => (
                <tr key={lead.id}>
                  <td>
                    <Link href={`/leads/${lead.id}`}>{lead.name || "Unnamed lead"}</Link>{" "}
                    <LeadBadges lead={lead} />
                  </td>
                  <td>
                    {lead.email ?? "—"}
                    <br />
                    {lead.phone ?? "—"}
                  </td>
                  <td>{lead.company || "—"}</td>
                  <td>{statusLabel(lead.status)}</td>
                  <td>{sourceLabel(lead.source)}</td>
                  <td>{lead.assignee_email ?? "Unassigned"}</td>
                  <td>{formatDateTime(lead.next_follow_up_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <nav className="pagination" aria-label="Lead pages">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span>
              Page {page} of {totalPages} ({data.total} leads)
            </span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
