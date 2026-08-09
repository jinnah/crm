"use client";

import {
  CalendarDays,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  Settings,
  UserRound,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { AuthGuard, useAuth } from "@/components/auth-context";
import { BrandMark, useBranding } from "@/components/brand-mark";
import { Button, ToastProvider } from "@/components/ui";
import { roleLabel } from "@/lib/roles";

const ICON_SIZE = 18;

type NavEntry = { href: string; label: string; icon: ReactNode; exact?: boolean };

const WORKSPACE_NAV: NavEntry[] = [
  { href: "/", label: "Today", icon: <LayoutDashboard size={ICON_SIZE} />, exact: true },
  { href: "/leads", label: "Leads", icon: <Users size={ICON_SIZE} /> },
  { href: "/calendar", label: "Calendar", icon: <CalendarDays size={ICON_SIZE} /> },
];

const ADMIN_NAV: NavEntry[] = [
  { href: "/settings", label: "Settings", icon: <Settings size={ICON_SIZE} /> },
  { href: "/fields", label: "Custom fields", icon: <ListChecks size={ICON_SIZE} /> },
  { href: "/users", label: "Users", icon: <UserRound size={ICON_SIZE} /> },
];

function isActive(entry: NavEntry, pathname: string): boolean {
  if (entry.exact) return pathname === entry.href;
  return pathname === entry.href || pathname.startsWith(`${entry.href}/`);
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const pathname = usePathname();
  const showAdmin = user.role === "owner";

  return (
    <>
      {WORKSPACE_NAV.map((entry) => (
        <Link
          key={entry.href}
          href={entry.href}
          className="nav-item"
          aria-current={isActive(entry, pathname) ? "page" : undefined}
          onClick={onNavigate}
        >
          {entry.icon}
          {entry.label}
        </Link>
      ))}
      {showAdmin && (
        <>
          <span className="nav-group-label">Administration</span>
          {ADMIN_NAV.map((entry) => (
            <Link
              key={entry.href}
              href={entry.href}
              className="nav-item"
              aria-current={isActive(entry, pathname) ? "page" : undefined}
              onClick={onNavigate}
            >
              {entry.icon}
              {entry.label}
            </Link>
          ))}
        </>
      )}
    </>
  );
}

function UserPanel({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout, loggingOut } = useAuth();
  const pathname = usePathname();
  return (
    <div className="shell-user">
      <div className="shell-identity">
        <span className="shell-identity-email">{user.email}</span>
        <span className="shell-identity-role">{roleLabel(user.role)}</span>
      </div>
      <Link
        href="/account"
        className="nav-item"
        aria-current={pathname === "/account" ? "page" : undefined}
        onClick={onNavigate}
      >
        <UserRound size={ICON_SIZE} />
        Account
      </Link>
      <Button className="btn-logout" onClick={() => void logout()} disabled={loggingOut}>
        <LogOut size={16} />
        {loggingOut ? "Logging out…" : "Log out"}
      </Button>
    </div>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const { logoutError } = useAuth();
  const branding = useBranding();
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Every drawer link closes it on navigation; Escape closes it too.
  useEffect(() => {
    if (!drawerOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setDrawerOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  const businessName = branding?.business_name ?? "Service CRM";
  // Tables and the calendar earn the wider track.
  const wide = pathname.startsWith("/calendar") || pathname === "/leads";

  const brand = (
    <span className="shell-brand">
      <BrandMark branding={branding} />
      <span className="brand-name">{businessName}</span>
    </span>
  );

  return (
    <div className="shell">
      <aside className="shell-sidebar">
        {brand}
        <nav className="shell-nav" aria-label="Main">
          <NavLinks />
        </nav>
        <UserPanel />
      </aside>

      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <header className="shell-topbar">
          <button
            type="button"
            className="menu-button"
            aria-label={drawerOpen ? "Close menu" : "Open menu"}
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen(!drawerOpen)}
          >
            {drawerOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          {brand}
        </header>

        {drawerOpen && (
          <>
            <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)} />
            <nav className="shell-drawer" aria-label="Main">
              {brand}
              <div className="shell-nav">
                <NavLinks onNavigate={() => setDrawerOpen(false)} />
              </div>
              <UserPanel onNavigate={() => setDrawerOpen(false)} />
            </nav>
          </>
        )}

        {logoutError !== null && (
          <p className="alert alert-error" role="alert" style={{ margin: "0.75rem" }}>
            {logoutError}
          </p>
        )}
        <main className="shell-main">
          <div className={wide ? "shell-content shell-content-wide" : "shell-content"}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <ToastProvider>
        <Shell>{children}</Shell>
      </ToastProvider>
    </AuthGuard>
  );
}
