"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Fired after the owner changes the logo or business name, so every mounted
 * shell surface refreshes at once — no navigation required. */
const BRANDING_EVENT = "crm:branding-changed";

export function notifyBrandingChanged(): void {
  window.dispatchEvent(new Event(BRANDING_EVENT));
}

export type PublicBranding = {
  business_name: string;
  has_logo: boolean;
  initials: string;
  updated_at: string | null;
};

/** Fetch the public branding; presentation only, so failures fall back
 * to a wordmark rather than blocking anything. Refetches whenever
 * notifyBrandingChanged() fires. */
export function useBranding(): PublicBranding | null {
  const [branding, setBranding] = useState<PublicBranding | null>(null);
  useEffect(() => {
    let cancelled = false;
    function load() {
      fetch(`${API_URL}/api/v1/public/branding`)
        .then((response) => (response.ok ? response.json() : null))
        .then((data: PublicBranding | null) => {
          if (!cancelled && data) setBranding(data);
        })
        .catch(() => {
          /* the wordmark fallback covers this */
        });
    }
    load();
    window.addEventListener(BRANDING_EVENT, load);
    return () => {
      cancelled = true;
      window.removeEventListener(BRANDING_EVENT, load);
    };
  }, []);
  return branding;
}

/**
 * The business's mark: the uploaded logo when one exists, otherwise clean
 * initials — never a broken image. The fixed square keeps the layout from
 * shifting while the image loads.
 */
export function BrandMark({
  branding,
  cacheKey,
}: {
  branding: PublicBranding | null;
  /** Bump to bypass the browser cache right after an upload. */
  cacheKey?: string | number;
}) {
  const [failed, setFailed] = useState(false);
  // A replaced logo carries a new updated_at, which doubles as the cache key.
  const key = cacheKey ?? branding?.updated_at ?? undefined;
  // A changed logo clears any previous load failure (render-time adjustment,
  // not an effect, per the React guidance for derived-state resets).
  const identity = `${branding?.has_logo ? 1 : 0}|${key ?? ""}`;
  const [lastIdentity, setLastIdentity] = useState(identity);
  if (lastIdentity !== identity) {
    setLastIdentity(identity);
    setFailed(false);
  }
  const showLogo = branding?.has_logo === true && !failed;
  const suffix = key !== undefined ? `?v=${encodeURIComponent(String(key))}` : "";
  return (
    <span className="brand-mark" aria-hidden="true">
      {showLogo ? (
        <img
          src={`${API_URL}/api/v1/public/logo${suffix}`}
          alt=""
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="brand-initials">{branding?.initials ?? "?"}</span>
      )}
    </span>
  );
}
