"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PasswordChangeForm } from "@/components/password-change-form";
import { useLogout } from "@/components/use-logout";
import { api, type SessionData } from "@/lib/api";

/**
 * Forced first-login password change. Users flagged with
 * must_change_password land here and cannot enter the CRM shell until the
 * change succeeds; the API rejects other protected actions regardless.
 * Logging out remains available.
 */
export default function ChangePasswordPage() {
  const router = useRouter();
  const [session, setSession] = useState<SessionData | null>(null);
  const {
    logout,
    pending: loggingOut,
    error: logoutError,
  } = useLogout(session?.csrf_token ?? null);

  useEffect(() => {
    let cancelled = false;
    void api<SessionData>("/auth/session").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        router.replace("/login");
        return;
      }
      if (!result.data.user.must_change_password) {
        router.replace("/");
        return;
      }
      setSession(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (session === null) {
    return (
      <main className="auth-page">
        <p className="page-status" role="status">
          Loading…
        </p>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Choose a new password</h1>
        <p>
          You are signed in as <strong>{session.user.email}</strong> with a temporary password.
          Set a new password to continue.
        </p>
        <PasswordChangeForm
          csrfToken={session.csrf_token}
          submitLabel="Set new password"
          onSuccess={() => router.replace("/")}
        />
        {logoutError !== null && (
          <p className="form-error" role="alert">
            {logoutError}
          </p>
        )}
        <button type="button" onClick={() => void logout()} disabled={loggingOut}>
          {loggingOut ? "Logging out…" : "Log out"}
        </button>
      </div>
    </main>
  );
}
