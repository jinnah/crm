"use client";

import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Button, FormDialog, InlineError } from "@/components/ui";
import {
  api,
  errorDetail,
  formatMinor,
  type CommercialDocument,
  type LineItem,
} from "@/lib/api";

/**
 * Draft line-item builder for quotes and invoices.
 *
 * Keyboard-operable throughout: plain inputs and buttons, explicit move
 * up/down controls for reordering. The preview totals below are calculated
 * locally for immediate feedback and ALWAYS replaced by the server's
 * authoritative figures when the draft is saved.
 */

type EditableLine = {
  description: string;
  quantity: string;
  unit: string;
  unitPrice: string;
  discountPercent: string;
  taxPercent: string;
};

function toEditable(line: LineItem): EditableLine {
  return {
    description: line.description,
    quantity: String(line.quantity_milli / 1000),
    unit: line.unit,
    unitPrice: (line.unit_price_minor / 100).toFixed(2),
    discountPercent: line.discount_bp ? String(line.discount_bp / 100) : "",
    taxPercent: line.tax_rate_bp ? String(line.tax_rate_bp / 100) : "",
  };
}

function emptyLine(): EditableLine {
  return {
    description: "",
    quantity: "1",
    unit: "",
    unitPrice: "",
    discountPercent: "",
    taxPercent: "",
  };
}

function parseLine(line: EditableLine): LineItem | string {
  if (!line.description.trim()) return "Every line needs a description.";
  const quantity = Number(line.quantity);
  if (!Number.isFinite(quantity) || quantity <= 0) return "Quantities must be positive numbers.";
  const unitPrice = Number(line.unitPrice);
  if (!Number.isFinite(unitPrice) || unitPrice < 0) return "Prices must be zero or more.";
  const discount = line.discountPercent === "" ? 0 : Number(line.discountPercent);
  if (!Number.isFinite(discount) || discount < 0 || discount > 100) {
    return "Discounts are between 0 and 100 percent.";
  }
  const tax = line.taxPercent === "" ? 0 : Number(line.taxPercent);
  if (!Number.isFinite(tax) || tax < 0 || tax > 50) {
    return "Tax rates are between 0 and 50 percent.";
  }
  return {
    description: line.description.trim(),
    quantity_milli: Math.round(quantity * 1000),
    unit: line.unit.trim(),
    unit_price_minor: Math.round(unitPrice * 100),
    discount_bp: Math.round(discount * 100),
    tax_rate_bp: Math.round(tax * 100),
  };
}

/** Local preview mirroring the server's documented rounding; display only. */
function previewTotals(lines: LineItem[], discountBp: number) {
  let subtotal = 0;
  let tax = 0;
  const keep = (10000 - discountBp) / 10000;
  for (const line of lines) {
    const net = Math.round(
      (line.quantity_milli * line.unit_price_minor * (10000 - line.discount_bp)) / 10_000_000,
    );
    subtotal += net;
    if (line.tax_rate_bp) tax += Math.round((net * keep * line.tax_rate_bp) / 10000);
  }
  const discount = Math.round((subtotal * discountBp) / 10000);
  return { subtotal, discount, tax, total: subtotal - discount + tax };
}

export function CommercialBuilder({
  document,
  csrfToken,
  onClose,
  onSaved,
}: {
  document: CommercialDocument;
  csrfToken: string;
  onClose: () => void;
  onSaved: (updated: CommercialDocument) => void;
}) {
  const [lines, setLines] = useState<EditableLine[]>(() =>
    document.lines.length > 0 ? document.lines.map(toEditable) : [emptyLine()],
  );
  const [discountPercent, setDiscountPercent] = useState(
    document.discount_bp ? String(document.discount_bp / 100) : "",
  );
  const [customerNotes, setCustomerNotes] = useState(document.customer_notes);
  const [terms, setTerms] = useState(document.terms);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  function update(index: number, changes: Partial<EditableLine>) {
    setDirty(true);
    setLines((current) =>
      current.map((line, i) => (i === index ? { ...line, ...changes } : line)),
    );
  }

  function move(index: number, delta: number) {
    setDirty(true);
    setLines((current) => {
      const next = [...current];
      const target = index + delta;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function remove(index: number) {
    setDirty(true);
    setLines((current) =>
      current.length === 1 ? [emptyLine()] : current.filter((_, i) => i !== index),
    );
  }

  const parsed = useMemo(() => lines.map(parseLine), [lines]);
  const parseProblem = parsed.find((line) => typeof line === "string") as string | undefined;
  const discountValue = discountPercent === "" ? 0 : Number(discountPercent);
  const discountBp =
    Number.isFinite(discountValue) && discountValue >= 0 && discountValue <= 100
      ? Math.round(discountValue * 100)
      : null;
  const preview =
    parseProblem === undefined && discountBp !== null
      ? previewTotals(parsed as LineItem[], discountBp)
      : null;

  async function save() {
    setError(null);
    if (parseProblem !== undefined) {
      setError(parseProblem);
      return;
    }
    if (discountBp === null) {
      setError("The document discount must be between 0 and 100 percent.");
      return;
    }
    setSaving(true);
    const result = await api<CommercialDocument>(
      `/jobs/${document.job_id}/commercial/${document.id}`,
      {
        method: "PATCH",
        csrfToken,
        body: {
          lines: parsed,
          discount_bp: discountBp,
          customer_notes: customerNotes,
          terms,
        },
      },
    );
    setSaving(false);
    if (!result.ok || result.data === null) {
      setError(errorDetail(result.data, "Unable to save the draft."));
      return;
    }
    setDirty(false);
    onSaved(result.data);
  }

  function close() {
    if (dirty && !window.confirm("Discard unsaved draft changes?")) return;
    onClose();
  }

  const kindLabel = document.kind === "quote" ? "quote" : "invoice";

  return (
    <FormDialog open title={`Edit ${kindLabel} ${document.number ?? "draft"}`} onClose={close}>
      <div className="builder-lines">
        {lines.map((line, index) => (
          <fieldset key={index} className="builder-line">
            <legend className="visually-hidden">Line {index + 1}</legend>
            <div className="form-field builder-description">
              <label htmlFor={`line-desc-${index}`}>Description</label>
              <input
                id={`line-desc-${index}`}
                value={line.description}
                maxLength={500}
                onChange={(event) => update(index, { description: event.target.value })}
              />
            </div>
            <div className="builder-numbers">
              <div className="form-field">
                <label htmlFor={`line-qty-${index}`}>Qty</label>
                <input
                  id={`line-qty-${index}`}
                  inputMode="decimal"
                  value={line.quantity}
                  onChange={(event) => update(index, { quantity: event.target.value })}
                />
              </div>
              <div className="form-field">
                <label htmlFor={`line-unit-${index}`}>Unit</label>
                <input
                  id={`line-unit-${index}`}
                  value={line.unit}
                  maxLength={20}
                  placeholder="hr, sq…"
                  onChange={(event) => update(index, { unit: event.target.value })}
                />
              </div>
              <div className="form-field">
                <label htmlFor={`line-price-${index}`}>Unit price</label>
                <input
                  id={`line-price-${index}`}
                  inputMode="decimal"
                  value={line.unitPrice}
                  onChange={(event) => update(index, { unitPrice: event.target.value })}
                />
              </div>
              <div className="form-field">
                <label htmlFor={`line-disc-${index}`}>Disc %</label>
                <input
                  id={`line-disc-${index}`}
                  inputMode="decimal"
                  value={line.discountPercent}
                  onChange={(event) => update(index, { discountPercent: event.target.value })}
                />
              </div>
              <div className="form-field">
                <label htmlFor={`line-tax-${index}`}>Tax %</label>
                <input
                  id={`line-tax-${index}`}
                  inputMode="decimal"
                  value={line.taxPercent}
                  onChange={(event) => update(index, { taxPercent: event.target.value })}
                />
              </div>
            </div>
            <div className="builder-line-actions">
              <Button
                size="sm"
                aria-label={`Move line ${index + 1} up`}
                disabled={index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowUp size={14} aria-hidden="true" />
              </Button>
              <Button
                size="sm"
                aria-label={`Move line ${index + 1} down`}
                disabled={index === lines.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown size={14} aria-hidden="true" />
              </Button>
              <Button
                size="sm"
                aria-label={`Remove line ${index + 1}`}
                onClick={() => remove(index)}
              >
                <Trash2 size={14} aria-hidden="true" />
              </Button>
            </div>
          </fieldset>
        ))}
      </div>
      <Button
        size="sm"
        onClick={() => {
          setDirty(true);
          setLines((current) => [...current, emptyLine()]);
        }}
      >
        <Plus size={14} aria-hidden="true" /> Add line
      </Button>

      <div className="form-field" style={{ marginTop: "1rem" }}>
        <label htmlFor="builder-discount">Document discount (%)</label>
        <input
          id="builder-discount"
          inputMode="decimal"
          style={{ maxWidth: "8rem" }}
          value={discountPercent}
          onChange={(event) => {
            setDirty(true);
            setDiscountPercent(event.target.value);
          }}
        />
      </div>
      <div className="form-field">
        <label htmlFor="builder-notes">Customer-facing notes</label>
        <textarea
          id="builder-notes"
          rows={2}
          maxLength={5000}
          value={customerNotes}
          onChange={(event) => {
            setDirty(true);
            setCustomerNotes(event.target.value);
          }}
        />
      </div>
      <div className="form-field">
        <label htmlFor="builder-terms">Terms</label>
        <textarea
          id="builder-terms"
          rows={2}
          maxLength={5000}
          value={terms}
          onChange={(event) => {
            setDirty(true);
            setTerms(event.target.value);
          }}
        />
      </div>

      {preview !== null && (
        <dl className="builder-preview" aria-label="Preview totals">
          <div>
            <dt>Subtotal</dt>
            <dd>{formatMinor(preview.subtotal, document.currency)}</dd>
          </div>
          {preview.discount > 0 && (
            <div>
              <dt>Discount</dt>
              <dd>-{formatMinor(preview.discount, document.currency)}</dd>
            </div>
          )}
          {preview.tax > 0 && (
            <div>
              <dt>Tax</dt>
              <dd>{formatMinor(preview.tax, document.currency)}</dd>
            </div>
          )}
          <div>
            <dt>Total (preview)</dt>
            <dd style={{ fontWeight: 700 }}>{formatMinor(preview.total, document.currency)}</dd>
          </div>
        </dl>
      )}
      <p className="form-help">
        The preview is calculated in your browser; saving replaces it with the server&apos;s
        authoritative totals.
      </p>

      {error !== null && <InlineError>{error}</InlineError>}
      <div className="dialog-actions">
        <Button onClick={close} disabled={saving}>
          Close
        </Button>
        <Button variant="primary" onClick={() => void save()} disabled={saving}>
          {saving ? "Saving…" : "Save draft"}
        </Button>
      </div>
    </FormDialog>
  );
}
