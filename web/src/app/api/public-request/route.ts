import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";

/**
 * Same-origin endpoint for the public request form.
 *
 * The browser never sees the form shared secret or the CRM inbound key: this
 * route runs on the server, adds the secret, and forwards the normalized
 * submission to the n8n website-form webhook.
 */

// Read at request time so runtime configuration is picked up without a rebuild.
function webhookUrl(): string {
  return process.env.N8N_FORM_WEBHOOK_URL ?? "http://n8n:5678/webhook/web-form";
}

function sharedSecret(): string {
  return process.env.FORM_SHARED_SECRET ?? "";
}

const MAX_BODY_BYTES = 16 * 1024;
const MAX_MESSAGE = 5000;

// Bounded in-memory throttle for this single instance: 5 submissions per IP
// per 10 minutes, with a hard cap on tracked keys so it cannot grow forever.
const WINDOW_MS = 10 * 60 * 1000;
const MAX_PER_WINDOW = 5;
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

function text(value: unknown, limit: number): string {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

export async function POST(request: Request) {
  if (!request.headers.get("content-type")?.includes("application/json")) {
    return NextResponse.json({ error: "Unsupported content type." }, { status: 415 });
  }
  if (throttled(clientIp(request))) {
    return NextResponse.json(
      { error: "Too many requests. Please try again later." },
      { status: 429 },
    );
  }

  const raw = await readBounded(request);
  if (raw === null) {
    return NextResponse.json({ error: "Request too large." }, { status: 413 });
  }
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }

  const name = text(body.name, 200);
  const email = text(body.email, 320);
  const phone = text(body.phone, 50);
  const subject = text(body.subject, 200);
  const message = text(body.message, MAX_MESSAGE);
  if (!message || (!email && !phone)) {
    return NextResponse.json(
      { error: "A message and either an email address or phone number are required." },
      { status: 422 },
    );
  }

  // A stable id makes browser retries idempotent all the way to the CRM.
  const submissionId = text(body.submission_id, 100) || randomUUID();

  try {
    const secret = sharedSecret();
    const response = await fetch(webhookUrl(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(secret ? { "X-Form-Secret": secret } : {}),
      },
      body: JSON.stringify({
        submission_id: submissionId,
        name,
        email,
        phone,
        subject,
        message,
        website: text(body.website, 100), // honeypot, forwarded for scoring
        form: text(body.form, 100) || "public-request",
        page: text(body.page, 500),
        campaign: text(body.campaign, 200),
        referrer: text(body.referrer, 500),
        submitted_at: new Date().toISOString(),
      }),
      signal: AbortSignal.timeout(30_000),
    });

    if (!response.ok) {
      // Never echo the submitted content back into logs or the response.
      console.warn(`Public form forward failed with status ${response.status}`);
      return NextResponse.json(
        { error: "We could not submit your request. Please try again." },
        { status: 502 },
      );
    }
    const data = (await response.json().catch(() => ({}))) as { replayed?: boolean };
    return NextResponse.json({
      status: "ok",
      submission_id: submissionId,
      duplicate: data.replayed === true,
    });
  } catch (error) {
    console.warn(`Public form forward error: ${(error as Error).name}`);
    return NextResponse.json(
      { error: "We could not submit your request. Please try again." },
      { status: 502 },
    );
  }
}
