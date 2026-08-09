"use client";

import { Plus, Search, UsersRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { LeadBadges, sourceLabel, statusLabel } from "@/components/lead-badges";
import { ResponseBadge } from "@/components/response-badge";
import { Badge, Button, Card, EmptyState, InlineError } from "@/components/ui";
import {
  api,
  errorDetail,
  LEAD_SOURCES,
  LEAD_STATUSES,
  type AssignableUser,
  type Lead,
  type LeadList,
} from "@/lib/api";
import { formatDateTime } from "@/lib/datetime";

const PAGE_SIZE = 25;

const STATUS_TONES: Record<string, "blue" | "teal" | "green" | "amber" | "gray"> = {
  new: "blue",
  contacted: "teal",
  qualified: "amber",
  won: "green",
  lost: "gray",
};

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
  const anyFilter =
    query !== "" || status !== "" || source !== "" || assignee !== "" || archived || needsReview;

  // Active filters as removable chips, so state is visible at a glance.
  const chips: Array<{ key: string; label: string; clear: () => void }> = [];
  if (query) chips.push({ key: "query", label: `Search: ${query}`, clear: () => { setQuery(""); setSearchInput(""); } });
  if (status) chips.push({ key: "status", label: statusLabel(status), clear: () => setStatus("") });
  if (source) chips.push({ key: "source", label: sourceLabel(source), clear: () => setSource("") });
  if (assignee === "unassigned") {
    chips.push({ key: "assignee", label: "Unassigned", clear: () => setAssignee("") });
  } else if (assignee) {
    const email = users.find((option) => option.id === assignee)?.email ?? "Assignee";
    chips.push({ key: "assignee", label: email, clear: () => setAssignee("") });
  }
  if (archived) chips.push({ key: "archived", label: "Archived", clear: () => setArchived(false) });
  if (needsReview) chips.push({ key: "review", label: "Needs review", clear: () => setNeedsReview(false) });

  return (
    <section>
      <header className="page-header">
        <div className="page-header-text">
          <h1>Leads</h1>
          <p>Every request and customer, searchable in one place.</p>
        </div>
        {canManage && (
          <Link className="button-link" href="/leads/new">
            <Plus size={16} aria-hidden="true" />
            New lead
          </Link>
        )}
      </header>

      <form className="toolbar" onSubmit={submitSearch}>
        <div className="form-field" style={{ flex: "1 1 14rem" }}>
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
        <Button type="submit" variant="primary">
          <Search size={16} aria-hidden="true" />
          Search
        </Button>
      </form>

      {chips.length > 0 && (
        <div className="filter-chips" aria-label="Active filters">
          {chips.map((chip) => (
            <button
              key={chip.key}
              type="button"
              onClick={() => {
                setPage(1);
                chip.clear();
              }}
            >
              {chip.label} ✕
            </button>
          ))}
        </div>
      )}

      {error !== null && <InlineError>{error}</InlineError>}
      {loading && (
        <p className="page-status" role="status">
          Loading leads…
        </p>
      )}

      {!loading && data !== null && data.items.length === 0 && (
        <Card flush>
          <EmptyState
            icon={<UsersRound size={40} aria-hidden="true" />}
            title={anyFilter ? "No leads match these filters" : "No leads yet"}
            description={
              anyFilter
                ? "Try removing a filter, or search for something broader."
                : "Leads arrive from the public form, SMS and calls — or add one yourself."
            }
            action={
              canManage && !anyFilter ? (
                <Link className="button-link" href="/leads/new">
                  <Plus size={16} aria-hidden="true" />
                  New lead
                </Link>
              ) : undefined
            }
          />
        </Card>
      )}

      {!loading && data !== null && data.items.length > 0 && (
        <>
          <Card flush className="table-card">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Contact</th>
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
                      <Link href={`/leads/${lead.id}`} style={{ fontWeight: 600 }}>
                        {lead.name || "Unnamed lead"}
                      </Link>{" "}
                      <LeadBadges lead={lead} showStatus={false} />{" "}
                      <ResponseBadge lead={lead} />
                      {lead.company !== "" && (
                        <div className="cell-secondary">{lead.company}</div>
                      )}
                    </td>
                    <td className="cell-secondary">
                      {lead.email ?? "—"}
                      <br />
                      {lead.phone ?? "—"}
                    </td>
                    <td>
                      <Badge tone={STATUS_TONES[lead.status] ?? "gray"}>
                        {statusLabel(lead.status)}
                      </Badge>
                    </td>
                    <td className="cell-secondary">{sourceLabel(lead.source)}</td>
                    <td className="cell-secondary">{lead.assignee_email ?? "Unassigned"}</td>
                    <td>
                      <FollowUpCell lead={lead} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <ul className="record-cards" aria-label="Leads">
            {data.items.map((lead) => (
              <li key={lead.id} className="record-card">
                <span className="record-card-title">
                  <Link href={`/leads/${lead.id}`}>{lead.name || "Unnamed lead"}</Link>
                  <Badge tone={STATUS_TONES[lead.status] ?? "gray"}>
                    {statusLabel(lead.status)}
                  </Badge>
                  <LeadBadges lead={lead} showStatus={false} />
                  <ResponseBadge lead={lead} />
                </span>
                <span className="record-card-meta">
                  {lead.email !== null && <span>{lead.email}</span>}
                  {lead.phone !== null && <span>{lead.phone}</span>}
                  <span>{sourceLabel(lead.source)}</span>
                  <span>{lead.assignee_email ?? "Unassigned"}</span>
                </span>
                {lead.next_follow_up_at !== null && (
                  <span className="record-card-meta">
                    <FollowUpCell lead={lead} />
                  </span>
                )}
              </li>
            ))}
          </ul>

          <nav className="pagination" aria-label="Lead pages">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <span>
              Page {page} of {totalPages} · {data.total} leads
            </span>
            <Button size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </nav>
        </>
      )}
    </section>
  );
}

function FollowUpCell({ lead }: { lead: Lead }) {
  // A stable "now" per mount keeps this render pure; follow-up urgency does
  // not need to tick live.
  const [now] = useState(() => Date.now());
  if (lead.next_follow_up_at === null) return <span className="cell-secondary">—</span>;
  const due = new Date(lead.next_follow_up_at);
  const overdue = !Number.isNaN(due.getTime()) && due.getTime() < now;
  return (
    <span style={{ display: "inline-flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
      {overdue && <Badge tone="amber">Overdue</Badge>}
      <span className="cell-secondary">{formatDateTime(lead.next_follow_up_at)}</span>
    </span>
  );
}
