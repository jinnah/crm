"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { AuthGuard, useAuth } from "@/components/auth-context";
import { roleLabel } from "@/lib/roles";

function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  return (
    <div className="shell">
      <header className="shell-header">
        <span className="shell-brand">Service CRM</span>
        <nav className="shell-nav" aria-label="Main">
          <Link href="/">Home</Link>
          <Link href="/account">Account</Link>
          {user.role === "owner" && <Link href="/users">Users</Link>}
        </nav>
        <div className="shell-user">
          <span className="shell-identity">
            {user.email} · {roleLabel(user.role)}
          </span>
          <button type="button" onClick={() => void logout()}>
            Log out
          </button>
        </div>
      </header>
      <main className="shell-main">{children}</main>
    </div>
  );
}

export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <Shell>{children}</Shell>
    </AuthGuard>
  );
}
