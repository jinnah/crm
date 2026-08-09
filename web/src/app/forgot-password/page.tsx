"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { api, errorDetail } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const result = await api<{ detail: string }>("/auth/forgot-password", {
      method: "POST",
      body: { email },
    });
    setSubmitting(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to process the request. Please try again."));
      return;
    }
    // The API responds identically whether or not the account exists.
    setConfirmation(
      errorDetail(
        result.data,
        "If an account exists for that email, a password reset link has been sent.",
      ),
    );
  }

  if (confirmation !== null) {
    return (
      <main className="auth-page">
        <div className="auth-card">
          <h1>Check your email</h1>
          <p role="status">{confirmation}</p>
          <p className="auth-links">
            <Link href="/login">Back to sign in</Link>
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <form className="auth-card" onSubmit={handleSubmit} noValidate>
        <h1>Forgot password</h1>
        <p>Enter your account email and we&apos;ll send a reset link if it matches an account.</p>
        <div className="form-field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        {error !== null && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting}>
          {submitting ? "Sending…" : "Send reset link"}
        </button>
        <p className="auth-links">
          <Link href="/login">Back to sign in</Link>
        </p>
      </form>
    </main>
  );
}
