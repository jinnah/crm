"use client";

import { useState } from "react";
import { useAuth } from "@/components/auth-context";
import { PasswordChangeForm } from "@/components/password-change-form";
import { roleLabel } from "@/lib/roles";

export default function AccountPage() {
  const { user, csrfToken, refresh } = useAuth();
  const [updated, setUpdated] = useState(false);

  return (
    <section>
      <h1>Account</h1>
      <p>
        {user.email} · {roleLabel(user.role)}
      </p>
      <h2>Change password</h2>
      {updated && (
        <p className="form-success" role="status">
          Password updated.
        </p>
      )}
      <PasswordChangeForm
        csrfToken={csrfToken}
        onSuccess={() => {
          setUpdated(true);
          // The session was rotated by the password change; refresh to pick
          // up the new CSRF token.
          void refresh();
        }}
      />
    </section>
  );
}
