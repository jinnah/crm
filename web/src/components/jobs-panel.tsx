"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { JobStatusBadge } from "@/components/job-badges";
import { Button, Card, FormDialog, InlineError } from "@/components/ui";
import {
  api,
  errorDetail,
  type Job,
  type JobDocument,
  type JobList,
  type Lead,
} from "@/lib/api";

/**
 * The customer's jobs, with each job's documents grouped under it — a
 * document is never shown detached from the job that owns it.
 */
export function JobsPanel({
  lead,
  csrfToken,
  canCreate,
}: {
  lead: Lead;
  csrfToken: string;
  canCreate: boolean;
}) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [documentsByJob, setDocumentsByJob] = useState<Record<string, JobDocument[]>>({});
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    void api<JobList>(`/jobs?lead_id=${lead.id}&page_size=100`).then(async (result) => {
      if (!result.ok || result.data === null) {
        setError(errorDetail(result.data, "Unable to load jobs."));
        return;
      }
      setJobs(result.data.items);
      const grouped: Record<string, JobDocument[]> = {};
      await Promise.all(
        result.data.items.map(async (job) => {
          const documents = await api<JobDocument[]>(`/jobs/${job.id}/documents`);
          if (documents.ok && documents.data !== null) {
            grouped[job.id] = documents.data.filter(
              (document) => document.deleted_at === null,
            );
          }
        }),
      );
      setDocumentsByJob(grouped);
    });
  }, [lead.id]);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <Card
      title="Jobs"
      description="Each piece of work with its own documents, quotes and invoices."
      actions={
        canCreate ? (
          <Button onClick={() => setCreating(true)}>
            <Plus size={16} aria-hidden="true" /> Create job
          </Button>
        ) : undefined
      }
    >
      {error !== null && <InlineError>{error}</InlineError>}
      {jobs === null ? (
        <p className="page-status" role="status">
          Loading jobs…
        </p>
      ) : jobs.length === 0 ? (
        <p className="card-empty">No jobs yet. Create one to start quoting this customer.</p>
      ) : (
        <ul className="document-list">
          {jobs.map((job) => {
            const documents = documentsByJob[job.id] ?? [];
            return (
              <li key={job.id} className="document-row" style={{ alignItems: "flex-start" }}>
                <span className="document-title">
                  <span>
                    <Link href={`/jobs/${job.id}`} style={{ fontWeight: 600 }}>
                      {job.job_number}
                    </Link>{" "}
                    <JobStatusBadge status={job.status} />
                  </span>
                  <span className="cell-secondary">
                    {job.title || job.service_type || "Untitled job"}
                    {job.service_address !== "" && ` · ${job.service_address}`}
                  </span>
                  {documents.length > 0 && (
                    <span className="cell-secondary">
                      Documents:{" "}
                      {documents.map((document, index) => (
                        <span key={document.id}>
                          {index > 0 && ", "}
                          {document.title}
                        </span>
                      ))}
                    </span>
                  )}
                </span>
                <Link className="btn btn-sm" href={`/jobs/${job.id}`}>
                  Open job
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      <FormDialog open={creating} title="Create job" onClose={() => setCreating(false)}>
        <CreateJobForm
          lead={lead}
          csrfToken={csrfToken}
          onCreated={() => {
            setCreating(false);
            reload();
          }}
        />
      </FormDialog>
    </Card>
  );
}

function CreateJobForm({
  lead,
  csrfToken,
  onCreated,
}: {
  lead: Lead;
  csrfToken: string;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [serviceType, setServiceType] = useState("");
  const [address, setAddress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    const result = await api("/jobs", {
      method: "POST",
      csrfToken,
      body: {
        lead_id: lead.id,
        title,
        service_type: serviceType,
        service_address: address,
      },
    });
    setSaving(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to create the job."));
      return;
    }
    onCreated();
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div className="form-field">
        <label htmlFor="job-title">Title</label>
        <input
          id="job-title"
          maxLength={200}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="job-service">Service</label>
        <input
          id="job-service"
          maxLength={200}
          placeholder="Roof replacement, HVAC install…"
          value={serviceType}
          onChange={(event) => setServiceType(event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="job-address">Service address</label>
        <input
          id="job-address"
          maxLength={300}
          value={address}
          onChange={(event) => setAddress(event.target.value)}
        />
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Creating…" : "Create job"}
        </Button>
      </div>
    </form>
  );
}
