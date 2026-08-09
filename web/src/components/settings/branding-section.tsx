"use client";

import { ImageUp, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  Button,
  Card,
  ConfirmDialog,
  InlineError,
  InlineSuccess,
} from "@/components/ui";
import { api, errorDetail } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_BYTES = 1024 * 1024;

type Branding = {
  business_name: string;
  has_logo: boolean;
  width: number | null;
  height: number | null;
  updated_at: string | null;
  initials: string;
};

/**
 * Owner-only logo management. A chosen file is previewed locally and nothing
 * is stored until Save; the server re-encodes whatever is uploaded, so the
 * preview is representative, not byte-identical.
 */
export function BrandingSection({ csrfToken }: { csrfToken: string }) {
  const [branding, setBranding] = useState<Branding | null>(null);
  const [pending, setPending] = useState<File | null>(null);
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  // Bumps the image URL after changes so the browser refetches.
  const [version, setVersion] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    void api<Branding>("/settings/branding").then((result) => {
      if (!cancelled && result.ok && result.data !== null) setBranding(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Object URLs are revoked when replaced or on unmount.
  useEffect(() => {
    return () => {
      if (pendingUrl !== null) URL.revokeObjectURL(pendingUrl);
    };
  }, [pendingUrl]);

  function choose(file: File | undefined) {
    setError(null);
    setNotice(null);
    if (!file) return;
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setError("Choose a PNG, JPEG or WebP image.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setError("The image must be 1 MB or smaller.");
      return;
    }
    if (pendingUrl !== null) URL.revokeObjectURL(pendingUrl);
    setPending(file);
    setPendingUrl(URL.createObjectURL(file));
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragActive(false);
    choose(event.dataTransfer.files?.[0]);
  }

  async function save() {
    if (pending === null) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`${API_URL}/api/v1/settings/branding/logo`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": pending.type, "X-CSRF-Token": csrfToken },
        body: pending,
      });
      const data = (await response.json().catch(() => null)) as Branding | { detail?: string } | null;
      if (!response.ok) {
        setError(
          (data as { detail?: string } | null)?.detail ?? "The logo could not be saved.",
        );
        return;
      }
      setBranding(data as Branding);
      setPending(null);
      if (pendingUrl !== null) URL.revokeObjectURL(pendingUrl);
      setPendingUrl(null);
      setVersion((value) => value + 1);
      setNotice("Logo saved. It now appears across the CRM and the customer pages.");
    } catch {
      setError("The logo could not be saved. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    setNotice(null);
    const result = await api<Branding>("/settings/branding/logo", {
      method: "DELETE",
      csrfToken,
    });
    setBusy(false);
    setConfirmingRemove(false);
    if (!result.ok || result.data === null) {
      setError(errorDetail(result.data, "The logo could not be removed."));
      return;
    }
    setBranding(result.data);
    setVersion((value) => value + 1);
    setNotice("Logo removed. The wordmark is shown instead.");
  }

  if (branding === null) {
    return (
      <p className="page-status" role="status">
        Loading branding…
      </p>
    );
  }

  const previewSrc =
    pendingUrl ??
    (branding.has_logo ? `${API_URL}/api/v1/public/logo?v=${version}` : null);

  return (
    <Card
      title="Logo"
      description="Shown in the navigation and on customer booking pages. Without one, your initials stand in."
    >
      {error !== null && <InlineError>{error}</InlineError>}
      {notice !== null && <InlineSuccess>{notice}</InlineSuccess>}

      <div className="logo-preview-row">
        <span className="logo-preview">
          {previewSrc !== null ? (
            // The preview is a local object URL or the served logo, never raw
            // uploaded bytes from anywhere else.
            <img src={previewSrc} alt="Logo preview" />
          ) : (
            <span className="brand-initials">{branding.initials}</span>
          )}
        </span>
        <div>
          {pending !== null ? (
            <>
              <p style={{ fontWeight: 600 }}>{pending.name}</p>
              <p className="form-help">
                {(pending.size / 1024).toFixed(0)} KB — not saved yet
              </p>
            </>
          ) : branding.has_logo ? (
            <>
              <p style={{ fontWeight: 600 }}>Current logo</p>
              <p className="form-help">
                {branding.width}×{branding.height}px, stored as PNG
              </p>
            </>
          ) : (
            <>
              <p style={{ fontWeight: 600 }}>No logo yet</p>
              <p className="form-help">The wordmark “{branding.initials}” is shown instead.</p>
            </>
          )}
        </div>
      </div>

      <div
        className={dragActive ? "dropzone active" : "dropzone"}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
      >
        <ImageUp size={28} aria-hidden="true" />
        <p>Drag an image here, or choose a file.</p>
        <Button onClick={() => inputRef.current?.click()} disabled={busy}>
          Choose file
        </Button>
        <p className="form-help">
          PNG, JPEG or WebP · up to 1 MB · resized to 512px and re-encoded on save
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_TYPES.join(",")}
          className="visually-hidden"
          aria-label="Choose a logo file"
          onChange={(event) => {
            choose(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
      </div>

      <div className="button-row" style={{ marginTop: "1rem" }}>
        {pending !== null && (
          <>
            <Button variant="primary" onClick={() => void save()} disabled={busy}>
              {busy ? "Saving…" : branding.has_logo ? "Replace logo" : "Save logo"}
            </Button>
            <Button
              onClick={() => {
                setPending(null);
                if (pendingUrl !== null) URL.revokeObjectURL(pendingUrl);
                setPendingUrl(null);
              }}
              disabled={busy}
            >
              Discard choice
            </Button>
          </>
        )}
        {branding.has_logo && pending === null && (
          <Button
            variant="destructive"
            onClick={() => setConfirmingRemove(true)}
            disabled={busy}
          >
            <Trash2 size={16} aria-hidden="true" />
            Remove logo
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirmingRemove}
        title="Remove the logo?"
        description="The CRM and customer pages will show your business initials instead. The image itself is deleted and cannot be restored here."
        confirmLabel="Remove logo"
        destructive
        busy={busy}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmingRemove(false)}
      />
    </Card>
  );
}
