"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { api, errorDetail } from "@/lib/api";
import { passwordPolicyError } from "@/lib/password";

function ResetPasswordForm() {
  const searchParams = useSearchParams();
  // Capture the token once into component memory; it is never written to
  // browser storage or logs.
  const [token] = useState(() => searchParams.get("token") ?? "");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Immediately drop the token from the visible URL and history entry so it
    // cannot leak through the address bar, history, or referrers.
    if (searchParams.get("token") !== null) {
      window.history.replaceState(window.history.state, "", window.location.pathname);
    }
  }, [searchParams]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const policyError = passwordPolicyError(newPassword);
    if (policyError !== null) {
      setError(policyError);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New password and confirmation do not match.");
      return;
    }
    setSubmitting(true);
    const result = await api("/auth/reset-password", {
      method: "POST",
      body: { token, new_password: newPassword },
    });
    setSubmitting(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Invalid or expired reset link."));
      return;
    }
    setDone(true);
  }

  if (token === "") {
    return (
      <div className="auth-card">
        <h1>Reset password</h1>
        <p role="alert" className="form-error">
          This reset link is invalid. Request a new one from the forgot-password page.
        </p>
        <p className="auth-links">
          <Link href="/forgot-password">Request a new link</Link>
        </p>
      </div>
    );
  }

  if (done) {
    return (
      <div className="auth-card">
        <h1>Password updated</h1>
        <p role="status">Your password has been reset. You can now sign in.</p>
        <p className="auth-links">
          <Link href="/login">Go to sign in</Link>
        </p>
      </div>
    );
  }

  return (
    <form className="auth-card" onSubmit={handleSubmit} noValidate>
      <h1>Reset password</h1>
      <div className="form-field">
        <label htmlFor="new-password">New password</label>
        <input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          aria-describedby="new-password-help"
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
        />
        <p id="new-password-help" className="form-help">
          At least 12 characters. Spaces and any characters are allowed.
        </p>
      </div>
      <div className="form-field">
        <label htmlFor="confirm-password">Confirm new password</label>
        <input
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          required
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
        />
      </div>
      {error !== null && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" disabled={submitting}>
        {submitting ? "Resetting…" : "Reset password"}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <main className="auth-page">
      <Suspense
        fallback={
          <p className="page-status" role="status">
            Loading…
          </p>
        }
      >
        <ResetPasswordForm />
      </Suspense>
    </main>
  );
}
