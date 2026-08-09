"use client";

import { Eye, EyeOff } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button, InlineError } from "@/components/ui";
import { api, errorDetail } from "@/lib/api";
import { passwordPolicyError } from "@/lib/password";

type Props = {
  csrfToken: string;
  onSuccess: () => void;
  submitLabel?: string;
};

/** One password input with a show/hide control that never leaves the row. */
function PasswordField({
  id,
  label,
  autoComplete,
  value,
  onChange,
  describedBy,
  help,
}: {
  id: string;
  label: string;
  autoComplete: string;
  value: string;
  onChange: (value: string) => void;
  describedBy?: string;
  help?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      <div className="password-input-row">
        <input
          id={id}
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          required
          aria-describedby={describedBy}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <Button
          size="sm"
          aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          aria-pressed={visible}
          onClick={() => setVisible(!visible)}
        >
          {visible ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
        </Button>
      </div>
      {help !== undefined && (
        <p id={describedBy} className="form-help">
          {help}
        </p>
      )}
    </div>
  );
}

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
      <PasswordField
        id="current-password"
        label="Current password"
        autoComplete="current-password"
        value={currentPassword}
        onChange={setCurrentPassword}
      />
      <PasswordField
        id="new-password"
        label="New password"
        autoComplete="new-password"
        value={newPassword}
        onChange={setNewPassword}
        describedBy="new-password-help"
        help="At least 12 characters. Spaces and any characters are allowed."
      />
      <PasswordField
        id="confirm-password"
        label="Confirm new password"
        autoComplete="new-password"
        value={confirmPassword}
        onChange={setConfirmPassword}
      />
      {error !== null && <InlineError>{error}</InlineError>}
      <Button type="submit" variant="primary" disabled={submitting}>
        {submitting ? "Saving…" : submitLabel}
      </Button>
    </form>
  );
}
