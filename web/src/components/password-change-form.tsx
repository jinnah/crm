"use client";

import { useState, type FormEvent } from "react";
import { api, errorDetail } from "@/lib/api";
import { passwordPolicyError } from "@/lib/password";

type Props = {
  csrfToken: string;
  onSuccess: () => void;
  submitLabel?: string;
};

export function PasswordChangeForm({ csrfToken, onSuccess, submitLabel = "Change password" }: Props) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
    const result = await api("/auth/change-password", {
      method: "POST",
      csrfToken,
      body: { current_password: currentPassword, new_password: newPassword },
    });
    setSubmitting(false);
    if (!result.ok) {
      setError(errorDetail(result.data, "Unable to change the password. Please try again."));
      return;
    }
    onSuccess();
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div className="form-field">
        <label htmlFor="current-password">Current password</label>
        <input
          id="current-password"
          type="password"
          autoComplete="current-password"
          required
          value={currentPassword}
          onChange={(event) => setCurrentPassword(event.target.value)}
        />
      </div>
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
        {submitting ? "Saving…" : submitLabel}
      </button>
    </form>
  );
}
