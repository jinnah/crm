"use client";

import { Download, FileText, ImageIcon, Plus, Upload } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth } from "@/components/auth-context";
import { CommercialBuilder } from "@/components/commercial-builder";
import {
  CommercialStatusBadge,
  EmailStatusBadge,
  JobStatusBadge,
} from "@/components/job-badges";
import {
  ActionsMenu,
  Badge,
  Button,
  Card,
  ConfirmDialog,
  FormDialog,
  InlineError,
  PageHeader,
  useToast,
} from "@/components/ui";
import {
  api,
  DOCUMENT_CATEGORIES,
  errorDetail,
  formatMinor,
  JOB_STATUS_LABELS,
  JOB_TRANSITIONS,
  type CommercialDocument,
  type EmailDeliveryRecord,
  type Job,
  type JobDocument,
  type Payment,
} from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function newKey(): string {
  return crypto.randomUUID();
}

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const { user, csrfToken } = useAuth();
  const canManage = user.role === "owner" || user.role === "manager";
  const toast = useToast();

  const [job, setJob] = useState<Job | null>(null);
  const [documents, setDocuments] = useState<JobDocument[]>([]);
  const [commercial, setCommercial] = useState<CommercialDocument[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [emails, setEmails] = useState<EmailDeliveryRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const reload = useCallback(() => {
    void api<Job>(`/jobs/${jobId}`).then((result) => {
      if (!result.ok || result.data === null) {
        if (result.status === 404) setNotFound(true);
        else setError(errorDetail(result.data, "Unable to load the job."));
        return;
      }
      setJob(result.data);
    });
    void api<JobDocument[]>(`/jobs/${jobId}/documents`).then((result) => {
      if (result.ok && result.data !== null) setDocuments(result.data);
    });
    void api<CommercialDocument[]>(`/jobs/${jobId}/commercial`).then((result) => {
      if (result.ok && result.data !== null) setCommercial(result.data);
    });
    void api<Payment[]>(`/jobs/${jobId}/payments`).then((result) => {
      if (result.ok && result.data !== null) setPayments(result.data);
    });
    void api<EmailDeliveryRecord[]>(`/jobs/${jobId}/emails`).then((result) => {
      if (result.ok && result.data !== null) setEmails(result.data);
    });
  }, [jobId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (notFound) {
    return (
      <section>
        <PageHeader title="Job not found" />
        <p className="page-status">
          This job does not exist or is not visible to you. <Link href="/jobs">Back to jobs</Link>
        </p>
      </section>
    );
  }
  if (job === null) {
    return (
      <p className="page-status" role="status">
        {error ?? "Loading job…"}
      </p>
    );
  }

  return (
    <section>
      <PageHeader
        title={
          <>
            {job.job_number}
            {job.title !== "" && <span className="page-title-detail"> — {job.title}</span>}
          </>
        }
        description={
          <>
            <JobStatusBadge status={job.status} />{" "}
            {job.archived_at !== null && <Badge>Archived</Badge>}{" "}
            <Link href={`/leads/${job.lead_id}`}>{job.lead_name ?? "Customer"}</Link>
            {job.service_address !== "" && ` · ${job.service_address}`}
          </>
        }
        actions={<JobActions job={job} csrfToken={csrfToken} canManage={canManage} onChanged={reload} />}
      />

      {error !== null && <InlineError>{error}</InlineError>}

      <div className="stack">
        <DocumentsCard
          job={job}
          documents={documents}
          csrfToken={csrfToken}
          canManage={canManage}
          onChanged={reload}
        />
        <CommercialCard
          job={job}
          documents={commercial}
          csrfToken={csrfToken}
          canManage={canManage}
          senderToast={toast}
          onChanged={reload}
        />
        <PaymentsCard
          job={job}
          payments={payments}
          commercial={commercial}
          csrfToken={csrfToken}
          canManage={canManage}
          onChanged={reload}
        />
        <EmailsCard emails={emails} />
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------------- */
/* Job status / archive actions                                             */
/* ----------------------------------------------------------------------- */

function JobActions({
  job,
  csrfToken,
  canManage,
  onChanged,
}: {
  job: Job;
  csrfToken: string;
  canManage: boolean;
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const toast = useToast();

  const nextStatuses = JOB_TRANSITIONS[job.status] ?? [];

  async function apply(action: () => Promise<{ ok: boolean; data: unknown }>) {
    setBusy(true);
    setFailure(null);
    const result = await action();
    setBusy(false);
    setConfirming(null);
    if (!result.ok) {
      toast(errorDetail(result.data, "The change was not applied."), "error");
      return;
    }
    onChanged();
  }

  return (
    <>
      {failure !== null && <InlineError>{failure}</InlineError>}
      <ActionsMenu label={`Actions for job ${job.job_number}`}>
        {(close) => (
          <>
            {nextStatuses.map((status) => (
              <button
                key={status}
                type="button"
                role="menuitem"
                onClick={() => {
                  close();
                  setConfirming(status);
                }}
              >
                Mark {JOB_STATUS_LABELS[status].toLowerCase()}
              </button>
            ))}
            {canManage && job.archived_at === null && (
              <button
                type="button"
                role="menuitem"
                className="menu-destructive"
                onClick={() => {
                  close();
                  setConfirming("archive");
                }}
              >
                Archive job
              </button>
            )}
            {canManage && job.archived_at !== null && (
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  close();
                  void apply(() => api(`/jobs/${job.id}/restore`, { method: "POST", csrfToken }));
                }}
              >
                Restore job
              </button>
            )}
          </>
        )}
      </ActionsMenu>

      <ConfirmDialog
        open={confirming !== null && confirming !== "archive"}
        title={`Mark this job ${confirming ? JOB_STATUS_LABELS[confirming as keyof typeof JOB_STATUS_LABELS]?.toLowerCase() : ""}?`}
        description="Job status changes are recorded on the customer timeline."
        confirmLabel="Change status"
        busy={busy}
        onConfirm={() =>
          void apply(() =>
            api(`/jobs/${job.id}/status`, {
              method: "POST",
              csrfToken,
              body: { status: confirming },
            }),
          )
        }
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        open={confirming === "archive"}
        title={`Archive job ${job.job_number}?`}
        description="The job and all its documents stay in history and can be restored at any time. Nothing is deleted."
        confirmLabel="Archive"
        destructive
        busy={busy}
        onConfirm={() =>
          void apply(() => api(`/jobs/${job.id}/archive`, { method: "POST", csrfToken }))
        }
        onCancel={() => setConfirming(null)}
      />
    </>
  );
}

/* ----------------------------------------------------------------------- */
/* Uploaded documents                                                       */
/* ----------------------------------------------------------------------- */

const SCAN_LABELS: Record<string, string> = {
  pending: "Scanning…",
  clean: "",
  infected: "Blocked: malware detected",
  failed: "Scan failed — quarantined",
};

function DocumentsCard({
  job,
  documents,
  csrfToken,
  canManage,
  onChanged,
}: {
  job: Job;
  documents: JobDocument[];
  csrfToken: string;
  canManage: boolean;
  onChanged: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [category, setCategory] = useState("other");
  const [uploading, setUploading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [editing, setEditing] = useState<JobDocument | null>(null);
  const [deleting, setDeleting] = useState<JobDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function upload(file: File) {
    setUploading(true);
    setFailure(null);
    const form = new FormData();
    form.append("file", file);
    form.append("category", category);
    try {
      const response = await fetch(`${API_URL}/api/v1/jobs/${job.id}/documents`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": csrfToken },
        body: form,
      });
      const data = (await response.json().catch(() => null)) as
        | (JobDocument & { detail?: string })
        | null;
      if (!response.ok) {
        setFailure(data?.detail ?? "The file could not be uploaded.");
        return;
      }
      if (data?.scan_state === "infected") {
        setFailure("The file was blocked by the malware scan and quarantined.");
      } else if (data?.scan_state === "failed") {
        setFailure(
          "The scanner is unavailable; the file is quarantined and can be re-scanned later.",
        );
      } else {
        toast("Document uploaded.");
      }
      onChanged();
    } catch {
      setFailure("The file could not be uploaded. Check your connection and try again.");
    } finally {
      setUploading(false);
    }
  }

  const visible = documents.filter((document) => document.deleted_at === null);

  return (
    <Card
      title="Documents"
      description="Customer paperwork for this job: PDF, PNG, JPEG or WebP, up to 15 MB. Files are scanned before they become available."
      actions={
        <div className="button-row">
          <select
            aria-label="Category for the next upload"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            {DOCUMENT_CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {value.charAt(0).toUpperCase() + value.slice(1)}
              </option>
            ))}
          </select>
          <Button onClick={() => inputRef.current?.click()} disabled={uploading}>
            <Upload size={16} aria-hidden="true" />
            {uploading ? "Uploading…" : "Upload document"}
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,image/png,image/jpeg,image/webp"
            className="visually-hidden"
            aria-label="Choose a document to upload"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
              event.target.value = "";
            }}
          />
        </div>
      }
    >
      {failure !== null && <InlineError>{failure}</InlineError>}
      {visible.length === 0 ? (
        <p className="card-empty">No documents yet.</p>
      ) : (
        <ul className="document-list">
          {visible.map((document) => (
            <li key={document.id} className="document-row">
              {document.has_preview ? (
                /* Server-generated normalized preview — never the original. */
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  className="document-thumb"
                  src={`${API_URL}/api/v1/jobs/${job.id}/documents/${document.id}/preview`}
                  alt=""
                />
              ) : (
                <span className="document-thumb document-thumb-icon" aria-hidden="true">
                  {document.content_type === "application/pdf" ? (
                    <FileText size={20} />
                  ) : (
                    <ImageIcon size={20} />
                  )}
                </span>
              )}
              <span className="document-title">
                <span style={{ fontWeight: 600 }}>{document.title}</span>
                <span className="cell-secondary">
                  {document.category} · {(document.byte_size / 1024).toFixed(0)} KB
                  {document.archived_at !== null && " · archived"}
                </span>
                {document.scan_state !== "clean" && (
                  <Badge tone={document.scan_state === "infected" ? "red" : "amber"}>
                    {SCAN_LABELS[document.scan_state]}
                  </Badge>
                )}
              </span>
              {document.scan_state === "clean" && (
                <a
                  className="btn btn-sm"
                  href={`${API_URL}/api/v1/jobs/${job.id}/documents/${document.id}/download`}
                >
                  <Download size={14} aria-hidden="true" /> Download
                </a>
              )}
              <ActionsMenu label={`Actions for document ${document.title}`}>
                {(close) => (
                  <>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        close();
                        setEditing(document);
                      }}
                    >
                      Edit details
                    </button>
                    {document.archived_at === null ? (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          close();
                          void api(`/jobs/${job.id}/documents/${document.id}/archive`, {
                            method: "POST",
                            csrfToken,
                          }).then(onChanged);
                        }}
                      >
                        Archive
                      </button>
                    ) : (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          close();
                          void api(`/jobs/${job.id}/documents/${document.id}/restore`, {
                            method: "POST",
                            csrfToken,
                          }).then(onChanged);
                        }}
                      >
                        Restore
                      </button>
                    )}
                    {canManage && (
                      <button
                        type="button"
                        role="menuitem"
                        className="menu-destructive"
                        onClick={() => {
                          close();
                          setDeleting(document);
                        }}
                      >
                        Delete upload
                      </button>
                    )}
                  </>
                )}
              </ActionsMenu>
            </li>
          ))}
        </ul>
      )}

      <FormDialog
        open={editing !== null}
        title="Edit document details"
        onClose={() => setEditing(null)}
      >
        {editing !== null && (
          <EditDocumentForm
            jobId={job.id}
            document={editing}
            csrfToken={csrfToken}
            onDone={() => {
              setEditing(null);
              onChanged();
            }}
          />
        )}
      </FormDialog>

      <ConfirmDialog
        open={deleting !== null}
        title={`Delete ${deleting?.title ?? ""}?`}
        description="The stored file is removed permanently; an audit record of the deletion is kept. Generated quotes, invoices and receipts can never be deleted this way."
        confirmLabel="Delete upload"
        destructive
        busy={busy}
        onConfirm={() => {
          if (deleting === null) return;
          setBusy(true);
          void api(`/jobs/${job.id}/documents/${deleting.id}/delete`, {
            method: "POST",
            csrfToken,
            body: { reason: "deleted from the job page" },
          }).then((result) => {
            setBusy(false);
            setDeleting(null);
            if (!result.ok) toast(errorDetail(result.data, "Unable to delete."), "error");
            onChanged();
          });
        }}
        onCancel={() => setDeleting(null)}
      />
    </Card>
  );
}

function EditDocumentForm({
  jobId,
  document,
  csrfToken,
  onDone,
}: {
  jobId: string;
  document: JobDocument;
  csrfToken: string;
  onDone: () => void;
}) {
  const [title, setTitle] = useState(document.title);
  const [category, setCategory] = useState(document.category);
  const [description, setDescription] = useState(document.description);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    const result = await api(`/jobs/${jobId}/documents/${document.id}`, {
      method: "PATCH",
      csrfToken,
      body: { title, category, description },
    });
    setSaving(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to save the details."));
      return;
    }
    onDone();
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div className="form-field">
        <label htmlFor="doc-title">Display title</label>
        <input
          id="doc-title"
          value={title}
          maxLength={200}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="doc-category">Category</label>
        <select
          id="doc-category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          {DOCUMENT_CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {value.charAt(0).toUpperCase() + value.slice(1)}
            </option>
          ))}
        </select>
      </div>
      <div className="form-field">
        <label htmlFor="doc-description">Description</label>
        <textarea
          id="doc-description"
          rows={2}
          maxLength={1000}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving…" : "Save details"}
        </Button>
      </div>
    </form>
  );
}

/* ----------------------------------------------------------------------- */
/* Quotes and invoices                                                      */
/* ----------------------------------------------------------------------- */

function CommercialCard({
  job,
  documents,
  csrfToken,
  canManage,
  senderToast,
  onChanged,
}: {
  job: Job;
  documents: CommercialDocument[];
  csrfToken: string;
  canManage: boolean;
  senderToast: (message: string, tone?: "default" | "error") => void;
  onChanged: () => void;
}) {
  const [building, setBuilding] = useState<CommercialDocument | null>(null);
  const [confirming, setConfirming] = useState<{ action: string; document: CommercialDocument } | null>(null);
  const [sending, setSending] = useState<CommercialDocument | null>(null);
  const [busy, setBusy] = useState(false);

  const quotesAndInvoices = documents.filter((document) => document.kind !== "receipt");

  async function createDraft(kind: "quote" | "invoice") {
    const result = await api<CommercialDocument>(`/jobs/${job.id}/commercial`, {
      method: "POST",
      csrfToken,
      body: { kind },
    });
    if (!result.ok || result.data === null) {
      senderToast(errorDetail(result.data, "Unable to create the draft."), "error");
      return;
    }
    onChanged();
    setBuilding(result.data);
  }

  async function run(path: string, success: string) {
    setBusy(true);
    const result = await api(`/jobs/${job.id}/commercial/${confirming?.document.id}/${path}`, {
      method: "POST",
      csrfToken,
      body: path === "void" ? { reason: "voided from the job page" } : undefined,
    });
    setBusy(false);
    setConfirming(null);
    if (!result.ok) {
      senderToast(errorDetail(result.data, "The action was not applied."), "error");
      return;
    }
    senderToast(success);
    onChanged();
  }

  return (
    <Card
      title="Quotes & invoices"
      description="Issued versions are immutable; corrections supersede or void, never rewrite."
      actions={
        <div className="button-row">
          <Button onClick={() => void createDraft("quote")}>
            <Plus size={16} aria-hidden="true" /> Quote
          </Button>
          <Button onClick={() => void createDraft("invoice")}>
            <Plus size={16} aria-hidden="true" /> Invoice
          </Button>
        </div>
      }
    >
      {quotesAndInvoices.length === 0 ? (
        <p className="card-empty">No quotes or invoices yet.</p>
      ) : (
        <ul className="document-list">
          {quotesAndInvoices.map((document) => {
            const editable =
              document.kind === "quote"
                ? ["draft", "sent", "viewed"].includes(document.status)
                : document.status === "draft";
            return (
              <li key={document.id} className="document-row">
                <span className="document-title">
                  <span style={{ fontWeight: 600 }}>
                    {document.kind === "quote" ? "Quote" : "Invoice"}{" "}
                    {document.number ?? "(draft)"}
                    {document.current_version > 1 && ` · v${document.current_version}`}
                  </span>
                  <span className="cell-secondary">
                    {formatMinor(document.total_minor, document.currency)}
                    {document.kind === "invoice" &&
                      document.amount_paid_minor > 0 &&
                      ` · paid ${formatMinor(document.amount_paid_minor, document.currency)}`}
                    {document.response_name !== null &&
                      ` · ${document.status} by ${document.response_name}`}
                  </span>
                </span>
                <CommercialStatusBadge status={document.status} />
                {document.current_version > 0 && (
                  <a
                    className="btn btn-sm"
                    href={`${API_URL}/api/v1/jobs/${job.id}/commercial/${document.id}/versions/${document.current_version}/pdf`}
                  >
                    <Download size={14} aria-hidden="true" /> PDF
                  </a>
                )}
                <ActionsMenu
                  label={`Actions for ${document.kind} ${document.number ?? "draft"}`}
                >
                  {(close) => (
                    <>
                      {editable && (
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            close();
                            setBuilding(document);
                          }}
                        >
                          Edit {document.status === "draft" ? "draft" : "and reissue"}
                        </button>
                      )}
                      {canManage && editable && document.lines.length > 0 && (
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            close();
                            setConfirming({ action: "issue", document });
                          }}
                        >
                          {document.current_version > 0 ? "Issue new version" : "Issue"}
                        </button>
                      )}
                      {canManage && document.current_version > 0 && document.status !== "voided" && (
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            close();
                            setSending(document);
                          }}
                        >
                          Send by email
                        </button>
                      )}
                      {canManage &&
                        document.kind === "quote" &&
                        document.status === "accepted" &&
                        document.converted_invoice_id === null && (
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => {
                              close();
                              setConfirming({ action: "convert", document });
                            }}
                          >
                            Convert to invoice
                          </button>
                        )}
                      {canManage && document.status !== "voided" && (
                        <button
                          type="button"
                          role="menuitem"
                          className="menu-destructive"
                          onClick={() => {
                            close();
                            setConfirming({ action: "void", document });
                          }}
                        >
                          Void
                        </button>
                      )}
                    </>
                  )}
                </ActionsMenu>
              </li>
            );
          })}
        </ul>
      )}

      {building !== null && (
        <CommercialBuilder
          document={building}
          csrfToken={csrfToken}
          onClose={() => setBuilding(null)}
          onSaved={(updated) => {
            setBuilding(updated);
            onChanged();
          }}
        />
      )}

      <ConfirmDialog
        open={confirming?.action === "issue"}
        title={`Issue this ${confirming?.document.kind}?`}
        description="Issuing assigns the final number and creates an immutable version with the exact PDF the customer will receive. Corrections later create a new version or void it — history is never rewritten."
        confirmLabel="Issue"
        busy={busy}
        onConfirm={() => void run("issue", "Issued.")}
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        open={confirming?.action === "convert"}
        title="Convert this accepted quote to an invoice?"
        description="The accepted line items are copied into a new invoice draft linked to this quote. Running it again returns the same invoice."
        confirmLabel="Convert"
        busy={busy}
        onConfirm={() => void run("convert", "Invoice created from the quote.")}
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        open={confirming?.action === "void"}
        title={`Void ${confirming?.document.kind} ${confirming?.document.number ?? "(draft)"}?`}
        description="A voided document stays in history, its customer links stop working, and it can no longer be sent or paid. Recorded payments must be reversed first."
        confirmLabel="Void"
        destructive
        busy={busy}
        onConfirm={() => void run("void", "Voided.")}
        onCancel={() => setConfirming(null)}
      />

      <FormDialog
        open={sending !== null}
        title={`Email ${sending?.kind ?? ""} ${sending?.number ?? ""}`}
        onClose={() => setSending(null)}
      >
        {sending !== null && (
          <SendEmailForm
            job={job}
            document={sending}
            csrfToken={csrfToken}
            onDone={(message) => {
              setSending(null);
              senderToast(message);
              onChanged();
            }}
          />
        )}
      </FormDialog>
    </Card>
  );
}

function SendEmailForm({
  job,
  document,
  csrfToken,
  onDone,
}: {
  job: Job;
  document: CommercialDocument;
  csrfToken: string;
  onDone: (message: string) => void;
}) {
  const [recipient, setRecipient] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // One explicit send action = one idempotency key; a retry of THIS form
  // submission can never queue a second email.
  const sendKey = useRef(newKey());

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSending(true);
    const result = await api(`/jobs/${job.id}/commercial/${document.id}/send`, {
      method: "POST",
      csrfToken,
      body: { recipient, send_key: sendKey.current },
    });
    setSending(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "The email could not be queued."));
      return;
    }
    onDone("Email queued for delivery.");
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <p className="card-description" style={{ marginBottom: "1rem" }}>
        The current version travels from this installation&apos;s verified sender with a secure
        customer link{document.kind === "quote" ? " that also lets the customer respond" : ""}.
      </p>
      <div className="form-field">
        <label htmlFor="send-recipient">Recipient email</label>
        <input
          id="send-recipient"
          type="email"
          required
          value={recipient}
          onChange={(event) => setRecipient(event.target.value)}
        />
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={sending}>
          {sending ? "Queuing…" : "Queue email"}
        </Button>
      </div>
    </form>
  );
}

/* ----------------------------------------------------------------------- */
/* Payments and receipts                                                    */
/* ----------------------------------------------------------------------- */

function PaymentsCard({
  job,
  payments,
  commercial,
  csrfToken,
  canManage,
  onChanged,
}: {
  job: Job;
  payments: Payment[];
  commercial: CommercialDocument[];
  csrfToken: string;
  canManage: boolean;
  onChanged: () => void;
}) {
  const [recording, setRecording] = useState(false);
  const [reversing, setReversing] = useState<Payment | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const receipts = new Map(
    commercial.filter((document) => document.kind === "receipt").map((r) => [r.id, r]),
  );
  const invoices = new Map(
    commercial.filter((document) => document.kind === "invoice").map((i) => [i.id, i]),
  );
  const payableInvoices = commercial.filter(
    (document) =>
      document.kind === "invoice" &&
      !["draft", "voided", "paid"].includes(document.status) &&
      document.amount_paid_minor < document.total_minor,
  );

  return (
    <Card
      title="Payments & receipts"
      description="Records money completed outside the CRM — cash, check, bank transfer or an externally processed card. Never card numbers or banking credentials."
      actions={
        canManage && payableInvoices.length > 0 ? (
          <Button onClick={() => setRecording(true)}>
            <Plus size={16} aria-hidden="true" /> Record payment
          </Button>
        ) : undefined
      }
    >
      {payments.length === 0 ? (
        <p className="card-empty">No payments recorded.</p>
      ) : (
        <ul className="document-list">
          {payments.map((payment) => {
            const receipt = payment.receipt_document_id
              ? receipts.get(payment.receipt_document_id)
              : undefined;
            const invoice = invoices.get(payment.invoice_id);
            return (
              <li key={payment.id} className="document-row">
                <span className="document-title">
                  <span style={{ fontWeight: 600 }}>
                    {formatMinor(payment.amount_minor, payment.currency)} ·{" "}
                    {payment.method.replace("_", " ")}
                  </span>
                  <span className="cell-secondary">
                    Invoice {invoice?.number ?? "—"}
                    {receipt !== undefined && ` · receipt ${receipt.number}`}
                    {payment.reference !== "" && ` · ref ${payment.reference}`}
                  </span>
                </span>
                {payment.voided_at !== null ? (
                  <Badge tone="red">Reversed</Badge>
                ) : (
                  <Badge tone="green">Posted</Badge>
                )}
                {receipt !== undefined && receipt.current_version > 0 && (
                  <a
                    className="btn btn-sm"
                    href={`${API_URL}/api/v1/jobs/${job.id}/commercial/${receipt.id}/versions/${receipt.current_version}/pdf`}
                  >
                    <Download size={14} aria-hidden="true" /> Receipt
                  </a>
                )}
                {canManage && payment.voided_at === null && (
                  <Button size="sm" onClick={() => setReversing(payment)}>
                    Reverse
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <FormDialog open={recording} title="Record a payment" onClose={() => setRecording(false)}>
        <RecordPaymentForm
          job={job}
          invoices={payableInvoices}
          csrfToken={csrfToken}
          onDone={(message) => {
            setRecording(false);
            toast(message);
            onChanged();
          }}
        />
      </FormDialog>

      <ConfirmDialog
        open={reversing !== null}
        title="Reverse this payment?"
        description="The payment is voided with an audit trail, its receipt is marked void (never erased) and the invoice balance reopens."
        confirmLabel="Reverse payment"
        destructive
        busy={busy}
        onConfirm={() => {
          if (reversing === null) return;
          setBusy(true);
          void api(`/jobs/${job.id}/payments/${reversing.id}/reverse`, {
            method: "POST",
            csrfToken,
            body: { reason: "reversed from the job page" },
          }).then((result) => {
            setBusy(false);
            setReversing(null);
            if (!result.ok) toast(errorDetail(result.data, "Unable to reverse."), "error");
            else toast("Payment reversed.");
            onChanged();
          });
        }}
        onCancel={() => setReversing(null)}
      />
    </Card>
  );
}

function RecordPaymentForm({
  job,
  invoices,
  csrfToken,
  onDone,
}: {
  job: Job;
  invoices: CommercialDocument[];
  csrfToken: string;
  onDone: (message: string) => void;
}) {
  const [invoiceId, setInvoiceId] = useState(invoices[0]?.id ?? "");
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("check");
  const [paidOn, setPaidOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const idempotencyKey = useRef(newKey());

  const invoice = invoices.find((candidate) => candidate.id === invoiceId);
  const remaining =
    invoice !== undefined ? invoice.total_minor - invoice.amount_paid_minor : 0;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0) {
      setError("Enter the payment amount.");
      return;
    }
    if (invoice === undefined) {
      setError("Choose an invoice.");
      return;
    }
    setSaving(true);
    const result = await api(`/jobs/${job.id}/commercial/${invoice.id}/payments`, {
      method: "POST",
      csrfToken,
      body: {
        amount_minor: Math.round(value * 100),
        currency: invoice.currency,
        method,
        paid_on: `${paidOn}T00:00:00Z`,
        reference,
        internal_note: note,
        idempotency_key: idempotencyKey.current,
      },
    });
    setSaving(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "The payment could not be recorded."));
      return;
    }
    onDone("Payment recorded; receipt issued.");
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div className="form-field">
        <label htmlFor="pay-invoice">Invoice</label>
        <select
          id="pay-invoice"
          value={invoiceId}
          onChange={(event) => setInvoiceId(event.target.value)}
        >
          {invoices.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.number} — remaining{" "}
              {formatMinor(candidate.total_minor - candidate.amount_paid_minor, candidate.currency)}
            </option>
          ))}
        </select>
      </div>
      <div className="form-field">
        <label htmlFor="pay-amount">Amount{invoice !== undefined ? ` (${invoice.currency})` : ""}</label>
        <input
          id="pay-amount"
          inputMode="decimal"
          aria-describedby="pay-amount-help"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
        />
        {invoice !== undefined && (
          <p id="pay-amount-help" className="form-help">
            Remaining balance: {formatMinor(remaining, invoice.currency)}. Partial payments are
            fine; overpayments are refused.
          </p>
        )}
      </div>
      <div className="form-field">
        <label htmlFor="pay-method">Method</label>
        <select id="pay-method" value={method} onChange={(event) => setMethod(event.target.value)}>
          <option value="cash">Cash</option>
          <option value="check">Check</option>
          <option value="bank_transfer">Bank transfer</option>
          <option value="card_external">Card (processed outside the CRM)</option>
          <option value="other">Other</option>
        </select>
      </div>
      <div className="form-field">
        <label htmlFor="pay-date">Payment date</label>
        <input
          id="pay-date"
          type="date"
          value={paidOn}
          onChange={(event) => setPaidOn(event.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="pay-reference">Reference (optional)</label>
        <input
          id="pay-reference"
          maxLength={100}
          aria-describedby="pay-reference-help"
          value={reference}
          onChange={(event) => setReference(event.target.value)}
        />
        <p id="pay-reference-help" className="form-help">
          A check number or transfer note. Never card numbers, security codes or bank
          credentials — those are rejected.
        </p>
      </div>
      <div className="form-field">
        <label htmlFor="pay-note">Internal note (optional)</label>
        <input
          id="pay-note"
          maxLength={500}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Recording…" : "Record payment"}
        </Button>
      </div>
    </form>
  );
}

/* ----------------------------------------------------------------------- */
/* Email history                                                            */
/* ----------------------------------------------------------------------- */

function EmailsCard({ emails }: { emails: EmailDeliveryRecord[] }) {
  if (emails.length === 0) return null;
  return (
    <Card
      title="Email history"
      description="Submitted means the provider accepted the message; delivered is confirmed separately."
    >
      <ul className="document-list">
        {emails.map((delivery) => (
          <li key={delivery.id} className="document-row">
            <span className="document-title">
              <span style={{ fontWeight: 600 }}>{delivery.subject}</span>
              <span className="cell-secondary">
                to {delivery.recipient} · {delivery.attach_pdf ? "PDF attached" : "secure link"}
                {delivery.failure_message !== null && ` · ${delivery.failure_message}`}
              </span>
            </span>
            <EmailStatusBadge status={delivery.status} />
          </li>
        ))}
      </ul>
    </Card>
  );
}
