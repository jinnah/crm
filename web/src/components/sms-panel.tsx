"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, errorDetail, type Activity, type Lead, type OutboundMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/datetime";

const MAX_SMS = 1600;

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `msg-${Math.random().toString(36).slice(2)}`;
}

type Entry = {
  key: string;
  direction: "inbound" | "outbound";
  body: string;
  at: string;
  status?: string;
  author?: string | null;
  purpose?: string;
  messageId?: string;
  error?: string | null;
};

const STATUS_LABELS: Record<string, string> = {
  pending: "Sending…",
  submitted: "Sent",
  delivered: "Delivered",
  failed: "Failed",
  unknown: "Unconfirmed",
};

const PURPOSE_LABELS: Record<string, string> = {
  human_reply: "",
  auto_acknowledgment: "auto acknowledgment",
  staff_alert: "staff alert",
};

/** Filtered communication view over the same stored history. */
export function SmsPanel({
  lead,
  activities,
  csrfToken,
  canSend,
  onChanged,
}: {
  lead: Lead;
  activities: Activity[];
  csrfToken: string;
  canSend: boolean;
  onChanged: () => void;
}) {
  const [messages, setMessages] = useState<OutboundMessage[] | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  const inFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    void api<OutboundMessage[]>(`/leads/${lead.id}/messages`).then((result) => {
      if (!cancelled && result.ok && Array.isArray(result.data)) setMessages(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [lead.id, reloadNonce]);

  const entries = useMemo<Entry[]>(() => {
    const inbound: Entry[] = activities
      .filter((activity) => activity.channel === "sms" && activity.direction === "inbound")
      .map((activity) => ({
        key: `in-${activity.id}`,
        direction: "inbound",
        body: activity.content,
        at: activity.occurred_at ?? activity.created_at,
      }));
    const outbound: Entry[] = (messages ?? []).map((message) => ({
      key: `out-${message.id}`,
      direction: "outbound",
      body: message.body,
      at: message.created_at,
      status: message.status,
      author: message.created_by_email,
      purpose: message.purpose,
      messageId: message.id,
      error: message.error_message,
    }));
    return [...inbound, ...outbound].sort((a, b) => a.at.localeCompare(b.at));
  }, [activities, messages]);

  async function send(text: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setError(null);
    setNotice(null);
    const result = await api<OutboundMessage>(`/leads/${lead.id}/messages`, {
      method: "POST",
      csrfToken,
      body: { body: text },
      // A fresh key per user-initiated send; duplicate clicks are blocked by
      // the in-flight guard above and by the server's pending check.
      idempotencyKey: newIdempotencyKey(),
    });
    inFlight.current = false;
    setSending(false);
    if (!result.ok || result.data === null) {
      setError(errorDetail(result.data, "The message could not be sent."));
      return;
    }
    if (result.data.status === "failed") {
      setError(result.data.error_message ?? "The message could not be delivered.");
    } else if (result.data.status === "unknown") {
      setNotice(
        "The messaging service did not confirm this message. It may or may not have been sent — check before resending.",
      );
    } else {
      setNotice("Message sent.");
      setDraft("");
    }
    setReloadNonce((value) => value + 1);
    onChanged();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) {
      setError("Write a message first.");
      return;
    }
    if (text.length > MAX_SMS) {
      setError(`Messages must be at most ${MAX_SMS} characters.`);
      return;
    }
    await send(text);
  }

  const archived = lead.archived_at !== null;
  const hasPhone = Boolean(lead.phone && lead.phone.startsWith("+"));

  return (
    <section className="sms-panel">
      <h2>SMS conversation</h2>
      {messages === null ? (
        <p className="page-status" role="status">
          Loading messages…
        </p>
      ) : entries.length === 0 ? (
        <p>No SMS messages yet.</p>
      ) : (
        <ul className="sms-thread">
          {entries.map((entry) => (
            <li
              key={entry.key}
              className={`sms-message ${
                entry.direction === "inbound" ? "sms-inbound" : "sms-outbound"
              }`}
            >
              <p className="sms-body">{entry.body}</p>
              <p className="sms-meta">
                {entry.direction === "inbound" ? "Received" : "Sent"} ·{" "}
                {formatDateTime(entry.at)}
                {entry.status ? (
                  <>
                    {" · "}
                    <span
                      className={
                        entry.status === "failed"
                          ? "sms-status-failed"
                          : entry.status === "unknown"
                            ? "sms-status-unknown"
                            : undefined
                      }
                    >
                      {STATUS_LABELS[entry.status] ?? entry.status}
                    </span>
                  </>
                ) : null}
                {entry.purpose && PURPOSE_LABELS[entry.purpose]
                  ? ` · ${PURPOSE_LABELS[entry.purpose]}`
                  : ""}
                {entry.author ? ` · ${entry.author}` : ""}
              </p>
              {entry.status === "failed" && canSend && !archived && (
                <button type="button" onClick={() => void send(entry.body)} disabled={sending}>
                  Retry send
                </button>
              )}
              {entry.status === "unknown" && (
                <p className="form-help">
                  Delivery was never confirmed. Resending may deliver the message twice — check
                  with the customer first.
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice !== null && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}

      {canSend && !archived && hasPhone && (
        <form onSubmit={handleSubmit} noValidate>
          <div className="form-field">
            <label htmlFor="sms-draft">Send an SMS to {lead.phone}</label>
            <textarea
              id="sms-draft"
              rows={3}
              maxLength={MAX_SMS}
              aria-describedby="sms-count"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <p id="sms-count" className="char-count">
              {draft.length} / {MAX_SMS} characters
            </p>
          </div>
          <button type="submit" disabled={sending || draft.trim() === ""}>
            {sending ? "Sending…" : "Send SMS"}
          </button>
        </form>
      )}
      {canSend && !archived && !hasPhone && (
        <p className="form-help">
          Add a phone number in international format (for example +15555550123) to send SMS.
        </p>
      )}
      {archived && <p className="form-help">Restore this lead to send messages.</p>}
    </section>
  );
}
