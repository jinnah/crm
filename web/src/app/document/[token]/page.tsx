"use client";

import { Download } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { BrandMark, useBranding } from "@/components/brand-mark";
import { Button, InlineError, InlineSuccess } from "@/components/ui";
import { formatMinor, formatQuantity } from "@/lib/api";

/**
 * The customer's secure view of ONE immutable document version. Everything
 * flows through the same-origin proxy; the capability never reaches the CRM
 * from the browser and grants nothing beyond this document.
 */

type LinePayload = {
  description: string;
  quantity_milli: number;
  unit: string;
  unit_price_minor: number;
  line_total_minor: number;
};

type DocumentInfo = {
  kind: "quote" | "invoice" | "receipt";
  number: string;
  status: string;
  business_name: string;
  payload: {
    currency: string;
    lines: LinePayload[];
    totals: {
      subtotal_minor: number;
      discount_total_minor: number;
      tax_total_minor: number;
      total_minor: number;
    };
    customer_notes: string;
    terms: string;
    valid_until: string | null;
    due_at: string | null;
    payment?: { amount_minor: number; method: string; invoice_number: string };
    job: { number: string; title: string };
    customer: { name: string };
  };
  responded_at: string | null;
  response_name: string | null;
  can_respond: boolean;
};

const KIND_TITLES = { quote: "Quote", invoice: "Invoice", receipt: "Receipt" };

export default function PublicDocumentPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const branding = useBranding();

  const [info, setInfo] = useState<DocumentInfo | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetch(`/api/public-document/${token}`)
      .then(async (response) => {
        const data = await response.json().catch(() => null);
        if (cancelled) return;
        if (!response.ok || data === null) {
          setFailure(
            (data as { detail?: string } | null)?.detail ?? "This document is not available.",
          );
          return;
        }
        setInfo(data as DocumentInfo);
      })
      .catch(() => {
        if (!cancelled) setFailure("This document could not be loaded. Please try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (failure !== null) {
    return (
      <main className="public-form">
        <div className="public-card">
          <p className="alert alert-error" role="alert">
            {failure}
          </p>
          <p className="form-help">
            If you expected a document here, ask the business to send a fresh link.
          </p>
        </div>
      </main>
    );
  }
  if (info === null) {
    return (
      <main className="public-form">
        <p className="page-status" role="status">
          Loading your document…
        </p>
      </main>
    );
  }

  const payload = info.payload;
  return (
    <main className="public-form">
      <div className="public-card">
        <header className="public-brand">
          <BrandMark branding={branding} />
          <div>
            <p className="public-brand-name">{info.business_name}</p>
            <p className="public-step">
              {KIND_TITLES[info.kind]} {info.number} · job {payload.job.number}
            </p>
          </div>
        </header>

        <h1 style={{ margin: "0.75rem 0 0.25rem" }}>
          {KIND_TITLES[info.kind]} {info.number}
        </h1>
        <p className="form-help">
          Prepared for {payload.customer.name}
          {info.kind === "quote" && payload.valid_until !== null &&
            ` · valid until ${new Date(payload.valid_until).toLocaleDateString()}`}
          {info.kind === "invoice" && payload.due_at !== null &&
            ` · due ${new Date(payload.due_at).toLocaleDateString()}`}
        </p>

        {info.responded_at !== null && (
          <InlineSuccess>
            This quote was {info.status} by {info.response_name} on{" "}
            {new Date(info.responded_at).toLocaleDateString()}.
          </InlineSuccess>
        )}

        {payload.lines.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <caption className="visually-hidden">Line items</caption>
              <thead>
                <tr>
                  <th scope="col">Description</th>
                  <th scope="col" style={{ textAlign: "right" }}>
                    Qty
                  </th>
                  <th scope="col" style={{ textAlign: "right" }}>
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {payload.lines.map((line, index) => (
                  <tr key={index}>
                    <td>{line.description}</td>
                    <td style={{ textAlign: "right" }}>
                      {formatQuantity(line.quantity_milli)}
                      {line.unit && ` ${line.unit}`}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {formatMinor(line.line_total_minor, payload.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <dl className="builder-preview" aria-label="Totals">
          <div>
            <dt>Subtotal</dt>
            <dd>{formatMinor(payload.totals.subtotal_minor, payload.currency)}</dd>
          </div>
          {payload.totals.discount_total_minor > 0 && (
            <div>
              <dt>Discount</dt>
              <dd>-{formatMinor(payload.totals.discount_total_minor, payload.currency)}</dd>
            </div>
          )}
          {payload.totals.tax_total_minor > 0 && (
            <div>
              <dt>Tax</dt>
              <dd>{formatMinor(payload.totals.tax_total_minor, payload.currency)}</dd>
            </div>
          )}
          <div>
            <dt style={{ fontWeight: 700 }}>Total</dt>
            <dd style={{ fontWeight: 700 }}>
              {formatMinor(payload.totals.total_minor, payload.currency)}
            </dd>
          </div>
        </dl>

        {payload.customer_notes !== "" && <p>{payload.customer_notes}</p>}
        {payload.terms !== "" && <p className="form-help">{payload.terms}</p>}

        <p style={{ margin: "1rem 0" }}>
          <a className="btn" href={`/api/public-document/${token}/pdf`}>
            <Download size={16} aria-hidden="true" /> Download PDF
          </a>
        </p>

        {info.can_respond && info.responded_at === null && (
          <ResponseForm token={token} onResponded={(updated) => setInfo(updated)} />
        )}
      </div>
    </main>
  );
}

function ResponseForm({
  token,
  onResponded,
}: {
  token: string;
  onResponded: (info: DocumentInfo) => void;
}) {
  const [typedName, setTypedName] = useState("");
  const [choice, setChoice] = useState<"accept" | "decline" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (choice === null) {
      setError("Choose accept or decline.");
      return;
    }
    if (!typedName.trim()) {
      setError("Type your name to confirm your response.");
      return;
    }
    setSubmitting(true);
    const response = await fetch(`/api/public-document/${token}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept: choice === "accept", typed_name: typedName, website: "" }),
    });
    const data = await response.json().catch(() => null);
    setSubmitting(false);
    if (!response.ok) {
      setError(
        (data as { detail?: string } | null)?.detail ?? "Your response could not be recorded.",
      );
      return;
    }
    // Re-fetch the document so the recorded response state is authoritative.
    const refreshed = await fetch(`/api/public-document/${token}`);
    if (refreshed.ok) onResponded((await refreshed.json()) as DocumentInfo);
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <h2 style={{ marginBottom: "0.5rem" }}>Respond to this quote</h2>
      <p className="form-help" style={{ marginBottom: "0.75rem" }}>
        Accepting tells the business to go ahead; it is not a payment and does not book a
        date by itself.
      </p>
      <div className="button-row" role="group" aria-label="Your response">
        <Button
          variant={choice === "accept" ? "primary" : undefined}
          aria-pressed={choice === "accept"}
          onClick={() => setChoice("accept")}
          type="button"
        >
          Accept quote
        </Button>
        <Button
          variant={choice === "decline" ? "primary" : undefined}
          aria-pressed={choice === "decline"}
          onClick={() => setChoice("decline")}
          type="button"
        >
          Decline
        </Button>
      </div>
      <div className="form-field" style={{ marginTop: "0.75rem" }}>
        <label htmlFor="response-name">Type your full name to confirm</label>
        <input
          id="response-name"
          required
          maxLength={200}
          value={typedName}
          onChange={(event) => setTypedName(event.target.value)}
        />
      </div>
      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Recording…" : "Record my response"}
        </Button>
      </div>
    </form>
  );
}
