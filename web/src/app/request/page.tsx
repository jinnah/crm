"use client";

import { useEffect, useState, type FormEvent } from "react";
import { BrandMark, useBranding } from "@/components/brand-mark";

type FormInfo = { form_title: string; form_intro: string; business_name: string };

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function newSubmissionId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `sub-${Math.random().toString(36).slice(2)}`;
}

export default function PublicRequestPage() {
  const branding = useBranding();
  const [info, setInfo] = useState<FormInfo | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  // A stable id per visitor session makes a double submit idempotent.
  const [submissionId] = useState(newSubmissionId);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<"sent" | "duplicate" | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/v1/public/form-info`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data: FormInfo | null) => {
        if (!cancelled && data) setInfo(data);
      })
      .catch(() => {
        /* presentation only; the form still works */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!message.trim()) {
      setError("Please tell us what you need.");
      return;
    }
    if (!email.trim() && !phone.trim()) {
      setError("Please provide an email address or a phone number so we can reply.");
      return;
    }
    setSubmitting(true);
    try {
      const response = await fetch("/api/public-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          submission_id: submissionId,
          name,
          email,
          phone,
          subject,
          message,
          website,
          page: typeof window === "undefined" ? "" : window.location.pathname,
          campaign:
            typeof window === "undefined"
              ? ""
              : new URLSearchParams(window.location.search).get("campaign") ?? "",
          referrer: typeof document === "undefined" ? "" : document.referrer,
        }),
      });
      const data = (await response.json().catch(() => ({}))) as {
        error?: string;
        duplicate?: boolean;
      };
      if (!response.ok) {
        setError(data.error ?? "We could not submit your request. Please try again.");
        return;
      }
      setOutcome(data.duplicate ? "duplicate" : "sent");
    } catch {
      setError("We could not reach the server. Please check your connection and try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (outcome !== null) {
    return (
      <main className="public-form">
        <div className="public-card">
            <div className="public-brand">
            <BrandMark branding={branding} />
            <span className="public-brand-name">
              {info?.business_name ?? branding?.business_name ?? ""}
            </span>
          </div>
          <h1>{outcome === "duplicate" ? "We already have your request" : "Request received"}</h1>
          <p role="status">
            {outcome === "duplicate"
              ? "This request was already submitted — there is no need to send it again. We will be in touch."
              : `Thanks! ${info?.business_name ?? "Our team"} will get back to you shortly.`}
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="public-form">
      <form className="public-card" onSubmit={handleSubmit} noValidate>
        <div className="public-brand">
          <BrandMark branding={branding} />
          <span className="public-brand-name">
            {info?.business_name ?? branding?.business_name ?? ""}
          </span>
        </div>
        <h1>{info?.form_title ?? "Request a quote"}</h1>
        {info?.form_intro ? <p>{info.form_intro}</p> : null}

        <div className="form-field">
          <label htmlFor="request-name">Name</label>
          <input
            id="request-name"
            autoComplete="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="request-email">Email</label>
          <input
            id="request-email"
            type="email"
            autoComplete="email"
            aria-describedby="contact-help"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="request-phone">Phone</label>
          <input
            id="request-phone"
            type="tel"
            autoComplete="tel"
            aria-describedby="contact-help"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
          />
          <p id="contact-help" className="form-help">
            Give us at least one way to reach you — an email address or a phone number.
          </p>
        </div>
        <div className="form-field">
          <label htmlFor="request-subject">What do you need?</label>
          <input
            id="request-subject"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="request-message">Message</label>
          <textarea
            id="request-message"
            rows={5}
            required
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
        </div>

        {/* Honeypot: hidden from people, tempting to bots. */}
        <div className="honeypot" aria-hidden="true">
          <label htmlFor="request-website">Leave this field empty</label>
          <input
            id="request-website"
            tabIndex={-1}
            autoComplete="off"
            value={website}
            onChange={(event) => setWebsite(event.target.value)}
          />
        </div>

        {error !== null && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting}>
          {submitting ? "Sending…" : "Send request"}
        </button>
        <p className="form-help">
          We use the details you provide only to respond to this request.
        </p>
      </form>
    </main>
  );
}
