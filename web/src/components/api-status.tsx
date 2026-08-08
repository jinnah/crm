"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Status = "checking" | "connected" | "unreachable";

const LABELS: Record<Status, string> = {
  checking: "Checking…",
  connected: "Connected",
  unreachable: "Unreachable",
};

export function ApiStatus() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/v1/health`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(() => {
        if (!cancelled) setStatus("connected");
      })
      .catch(() => {
        if (!cancelled) setStatus("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <p data-status={status}>
      API status: <strong>{LABELS[status]}</strong>
    </p>
  );
}
