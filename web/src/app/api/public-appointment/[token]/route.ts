import { NextResponse } from "next/server";

/**
 * Same-origin proxy for the customer's appointment page.
 *
 * The capability token in the path is the only authority the CRM accepts, so
 * this route forwards it and nothing else: no appointment id, no lead id, no
 * user id. The token is never logged.
 */

function apiBase(): string {
  return process.env.CRM_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://api:8000";
}

const MAX_BODY_BYTES = 4 * 1024;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{16,200}$/;

const WINDOW_MS = 10 * 60 * 1000;
const MAX_PER_WINDOW = 20;
const MAX_TRACKED_IPS = 5000;
const attempts = new Map<string, number[]>();

function throttled(ip: string): boolean {
  const now = Date.now();
  for (const [key, times] of attempts) {
    const fresh = times.filter((time) => now - time < WINDOW_MS);
    if (fresh.length === 0) attempts.delete(key);
    else attempts.set(key, fresh);
  }
  const recent = (attempts.get(ip) ?? []).filter((time) => now - time < WINDOW_MS);
  if (recent.length >= MAX_PER_WINDOW) return true;
  if (!attempts.has(ip) && attempts.size >= MAX_TRACKED_IPS) return true; // fail closed
  recent.push(now);
  attempts.set(ip, recent);
  return false;
}

function clientIp(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return request.headers.get("x-real-ip") ?? "unknown";
}

/** Read the body with a hard byte ceiling, before any JSON parsing. */
async function readBounded(request: Request): Promise<string | null> {
  const reader = request.body?.getReader();
  if (!reader) return "";
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function forward(path: string, init: RequestInit): Promise<NextResponse> {
  try {
    const response = await fetch(`${apiBase()}/api/v1/public/appointments/${path}`, {
      ...init,
      signal: AbortSignal.timeout(30_000),
    });
    const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    return NextResponse.json(data, {
      status: response.status,
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    console.warn(`Appointment management forward error: ${(error as Error).name}`);
    return NextResponse.json(
      { detail: "We could not reach the booking service. Please try again." },
      { status: 502 },
    );
  }
}

export async function GET(_request: Request, context: { params: Promise<{ token: string }> }) {
  const { token } = await context.params;
  if (!TOKEN_PATTERN.test(token)) {
    return NextResponse.json({ detail: "This appointment link is not valid." }, { status: 404 });
  }
  return forward(token, { method: "GET", headers: { Accept: "application/json" } });
}

export async function POST(request: Request, context: { params: Promise<{ token: string }> }) {
  const { token } = await context.params;
  if (!TOKEN_PATTERN.test(token)) {
    return NextResponse.json({ detail: "This appointment link is not valid." }, { status: 404 });
  }
  if (!request.headers.get("content-type")?.includes("application/json")) {
    return NextResponse.json({ detail: "Unsupported content type." }, { status: 415 });
  }
  if (throttled(clientIp(request))) {
    return NextResponse.json(
      { detail: "Too many attempts. Please try again later." },
      { status: 429 },
    );
  }

  const raw = await readBounded(request);
  if (raw === null) {
    return NextResponse.json({ detail: "Request too large." }, { status: 413 });
  }
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ detail: "Invalid request." }, { status: 400 });
  }

  if (body.action === "cancel") {
    return forward(`${token}/cancel`, { method: "POST", headers: { Accept: "application/json" } });
  }
  const startAt = typeof body.start_at === "string" ? body.start_at.slice(0, 40) : "";
  if (body.action !== "reschedule" || !startAt) {
    return NextResponse.json({ detail: "Choose a new time first." }, { status: 422 });
  }
  const website = typeof body.website === "string" ? body.website.slice(0, 100) : "";
  // Only the new time and the honeypot travel onward.
  return forward(`${token}/reschedule`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start_at: startAt, website }),
  });
}
