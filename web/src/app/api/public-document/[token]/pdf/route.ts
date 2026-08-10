import { NextResponse } from "next/server";

/** Streams the exact immutable PDF for a valid capability, via the fixed
 * internal path with the server-only credential. Nosniff, attachment-only. */

function apiBase(): string {
  return process.env.CRM_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://api:8000";
}

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{16,200}$/;

export async function GET(_request: Request, context: { params: Promise<{ token: string }> }) {
  const { token } = await context.params;
  if (!TOKEN_PATTERN.test(token)) {
    return NextResponse.json({ detail: "This document link is not valid." }, { status: 404 });
  }
  try {
    const response = await fetch(`${apiBase()}/api/v1/internal/documents/pdf`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Key": process.env.INTERNAL_BFF_KEY ?? "",
      },
      body: JSON.stringify({ token }),
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({ detail: "Not available." }));
      return NextResponse.json(data, { status: response.status });
    }
    const bytes = await response.arrayBuffer();
    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition":
          response.headers.get("content-disposition") ?? 'attachment; filename="document.pdf"',
        "Cache-Control": "private, no-store",
      },
    });
  } catch (error) {
    console.warn(`Public document PDF forward error: ${(error as Error).name}`);
    return NextResponse.json(
      { detail: "We could not reach the document service. Please try again." },
      { status: 502 },
    );
  }
}
