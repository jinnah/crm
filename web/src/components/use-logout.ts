"use client";

import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";

/**
 * Logout with honest failure handling: the user is redirected to the login
 * page only after the API confirms the session is gone (or was already gone);
 * a failed call surfaces an error and allows retry. Duplicate submissions are
 * suppressed while one is pending. The HttpOnly cookie itself is only ever
 * cleared by the API.
 */
export function useLogout(csrfToken: string | null) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const logout = useCallback(async () => {
    if (inFlight.current || csrfToken === null) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    const result = await api("/auth/logout", { method: "POST", csrfToken });
    inFlight.current = false;
    setPending(false);
    // 401 means the session is already invalid — the user is logged out.
    if (result.ok || result.status === 401) {
      router.replace("/login");
      return;
    }
    setError("Unable to log out. Please try again.");
  }, [csrfToken, router]);

  return { logout, pending, error };
}
