"use client";

import { useState } from "react";
import { useAuth } from "@/components/auth-context";
import { PasswordChangeForm } from "@/components/password-change-form";
import { Card, InlineSuccess, PageHeader } from "@/components/ui";
import { roleLabel } from "@/lib/roles";

export default function AccountPage() {
  const { user, csrfToken, refresh } = useAuth();
  const [updated, setUpdated] = useState(false);
  const initial = (user.email[0] ?? "?").toUpperCase();

  return (
    <section className="narrow-form">
      <PageHeader title="Account" description="Who you are signed in as, and your security." />

      <div className="stack">
        <Card title="Profile">
          <div className="profile-row">
            <span className="avatar" aria-hidden="true">
              {initial}
            </span>
            <div>
              <p style={{ fontWeight: 600 }}>{user.email}</p>
              <p className="card-description">{roleLabel(user.role)}</p>
            </div>
          </div>
        </Card>

        <Card
          title="Security"
          description="Changing your password signs out your other sessions."
        >
          {updated && <InlineSuccess>Password updated.</InlineSuccess>}
          <PasswordChangeForm
            csrfToken={csrfToken}
            onSuccess={() => {
              setUpdated(true);
              // The session was rotated by the password change; refresh to pick
              // up the new CSRF token.
              void refresh();
            }}
          />
        </Card>
      </div>
    </section>
  );
}
