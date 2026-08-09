"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, type SessionData, type SessionUser } from "@/lib/api";

type AuthState = {
  user: SessionUser;
  csrfToken: string;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used inside AuthGuard");
  }
  return value;
}

/**
 * Client-side gate for the protected CRM shell. Redirects unauthenticated
 * users to the login page and users with a pending forced password change to
 * that flow. The API independently enforces both rules.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<SessionData | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void api<SessionData>("/auth/session").then((result) => {
      if (cancelled) return;
      if (!result.ok || result.data === null) {
        router.replace("/login");
        return;
      }
      if (result.data.user.must_change_password) {
        router.replace("/change-password");
        return;
      }
      setSession(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [router, reloadNonce]);

  const refresh = useCallback(async () => {
    setReloadNonce((value) => value + 1);
  }, []);

  const logout = useCallback(async () => {
    if (session !== null) {
      await api("/auth/logout", { method: "POST", csrfToken: session.csrf_token });
    }
    router.replace("/login");
  }, [router, session]);

  if (session === null) {
    return (
      <p className="page-status" role="status">
        Loading…
      </p>
    );
  }

  return (
    <AuthContext.Provider
      value={{ user: session.user, csrfToken: session.csrf_token, refresh, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}
