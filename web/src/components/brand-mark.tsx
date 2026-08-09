"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type PublicBranding = {
  business_name: string;
  has_logo: boolean;
  initials: string;
};

/** Fetch the public branding once; presentation only, so failures fall back
 * to a wordmark rather than blocking anything. */
export function useBranding(): PublicBranding | null {
  const [branding, setBranding] = useState<PublicBranding | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/v1/public/branding`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data: PublicBranding | null) => {
        if (!cancelled && data) setBranding(data);
      })
      .catch(() => {
        /* the wordmark fallback covers this */
      });
    return () => {
      cancelled = true;
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
  const showLogo = branding?.has_logo === true && !failed;
  const suffix = cacheKey !== undefined ? `?v=${cacheKey}` : "";
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
