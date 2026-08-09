/**
 * Executes the actual Code-node JavaScript from the committed n8n workflow
 * files against realistic provider fixtures, so normalization and signature
 * logic are tested exactly as they will run inside n8n.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { describe, expect, test } from "vitest";

const WORKFLOWS_DIR = path.resolve(__dirname, "../../../n8n/workflows");

const ENV = {
  CRM_API_URL: "http://api:8000",
  CRM_INBOUND_API_KEY: "test-inbound-key-not-real",
  TWILIO_AUTH_TOKEN: "test-twilio-auth-token-not-real",
  TWILIO_WEBHOOK_BASE_URL: "http://localhost:5678",
  META_APP_SECRET: "test-meta-app-secret-not-real",
  META_VERIFY_TOKEN: "test-meta-verify-token",
  FORM_SHARED_SECRET: "",
};

type Item = { json: Record<string, unknown>; binary?: Record<string, unknown> };
type Workflow = {
  name: string;
  nodes: Array<{
    name: string;
    type: string;
    parameters: Record<string, unknown>;
    retryOnFail?: boolean;
    maxTries?: number;
  }>;
};

function loadWorkflow(file: string): Workflow {
  return JSON.parse(fs.readFileSync(path.join(WORKFLOWS_DIR, file), "utf8"));
}

function runCodeNode(workflow: Workflow, nodeName: string, items: Item[], env = ENV) {
  const node = workflow.nodes.find((candidate) => candidate.name === nodeName);
  if (!node) throw new Error(`node ${nodeName} not found in ${workflow.name}`);
  const nodeRequire = createRequire(import.meta.url);
  // Test-only execution of our own version-controlled Code-node source (the
  // same code n8n runs). Nothing user-supplied or interpolated reaches this
  // function body.
  const fn = new Function("$input", "$env", "require", "Buffer", String(node.parameters.jsCode));
  // Workflow outputs are dynamic by nature; tests assert their shapes, so the
  // cast declares the fields the assertions reach for.
  return fn({ all: () => items }, env, nodeRequire, Buffer) as Array<{
    json: Record<string, unknown> & { event: Record<string, unknown> };
  }>;
}

function twilioSignature(params: Record<string, string>, webhookPath: string): string {
  const url = `${ENV.TWILIO_WEBHOOK_BASE_URL}/webhook/${webhookPath}`;
  const sorted = Object.keys(params)
    .sort()
    .map((key) => key + params[key])
    .join("");
  return crypto.createHmac("sha1", ENV.TWILIO_AUTH_TOKEN).update(url + sorted, "utf8").digest("base64");
}

function metaItem(payload: unknown, sign = true): Item {
  const raw = JSON.stringify(payload);
  const signature = `sha256=${crypto
    .createHmac("sha256", ENV.META_APP_SECRET)
    .update(raw, "utf8")
    .digest("hex")}`;
  return {
    json: {
      headers: { "x-hub-signature-256": sign ? signature : "sha256=badbadbad" },
      body: payload,
    },
    binary: { data: { data: Buffer.from(raw, "utf8").toString("base64") } },
  };
}

describe("website form intake", () => {
  const workflow = loadWorkflow("website-form-intake.json");
  const valid = {
    json: {
      headers: {},
      body: {
        submission_id: "sub-0001",
        name: "Pat Form",
        email: "pat@example.com",
        message: "Please quote my roof",
        form: "contact-us",
        page: "/roofing",
        campaign: "spring",
        submitted_at: "2026-08-08T12:00:00Z",
      },
    },
  };

  test("normalizes a valid submission with deterministic idempotency", () => {
    const [first] = runCodeNode(workflow, "Normalize", [valid]);
    const [second] = runCodeNode(workflow, "Normalize", [valid]);
    expect(first.json.reject).toBe(false);
    expect(first.json.idempotency_key).toBe("form:sub-0001");
    expect(second.json.idempotency_key).toBe("form:sub-0001"); // stable across retries
    expect(first.json.event).toMatchObject({
      channel: "web_form",
      external_event_id: "sub-0001",
      sender_email: "pat@example.com",
      content: "Please quote my roof",
      received_at: "2026-08-08T12:00:00Z",
      metadata: { form: "contact-us", page: "/roofing", campaign: "spring" },
    });
  });

  test("rejects submissions missing required fields", () => {
    const [result] = runCodeNode(workflow, "Normalize", [
      { json: { headers: {}, body: { submission_id: "sub-2", message: "hi" } } },
    ]);
    expect(result.json.reject).toBe(true);
    expect(result.json.status).toBe(422);
  });

  test("rejects oversized payloads", () => {
    const big = structuredClone(valid);
    (big.json.body as Record<string, string>).message = "x".repeat(11_000);
    const [result] = runCodeNode(workflow, "Normalize", [big]);
    expect(result.json).toMatchObject({ reject: true, status: 413 });
  });

  test("silently drops honeypot submissions", () => {
    const bot = structuredClone(valid);
    (bot.json.body as Record<string, string>).website = "http://spam.example";
    const [result] = runCodeNode(workflow, "Normalize", [bot]);
    expect(result.json).toMatchObject({ reject: true, status: 200 });
  });

  test("enforces the shared secret when configured", () => {
    const env = { ...ENV, FORM_SHARED_SECRET: "form-secret" };
    const [denied] = runCodeNode(workflow, "Normalize", [valid], env);
    expect(denied.json).toMatchObject({ reject: true, status: 403 });
    const ok = structuredClone(valid);
    (ok.json.headers as Record<string, string>)["x-form-secret"] = "form-secret";
    const [allowed] = runCodeNode(workflow, "Normalize", [ok], env);
    expect(allowed.json.reject).toBe(false);
  });
});

describe("twilio sms", () => {
  const workflow = loadWorkflow("twilio-sms.json");
  const params = {
    MessageSid: "SMfake00000000000000000000000001",
    From: "+15550100001",
    To: "+15550209999",
    Body: "Do you install metal roofs?",
    SmsStatus: "received",
    FromCity: "SYRACUSE",
  };

  test("accepts a correctly signed SMS and normalizes it", () => {
    const item = {
      json: { headers: { "x-twilio-signature": twilioSignature(params, "twilio-sms") }, body: params },
    };
    const [result] = runCodeNode(workflow, "Normalize", [item]);
    expect(result.json.reject).toBe(false);
    expect(result.json.idempotency_key).toBe(`twilio:sms:${params.MessageSid}`);
    expect(result.json.event).toMatchObject({
      channel: "sms",
      provider: "twilio",
      external_event_id: params.MessageSid,
      sender_phone: "+15550100001",
      content: "Do you install metal roofs?",
      metadata: { to: "+15550209999", message_status: "received" },
    });
  });

  test("rejects an invalid signature", () => {
    const item = { json: { headers: { "x-twilio-signature": "forged" }, body: params } };
    const [result] = runCodeNode(workflow, "Normalize", [item]);
    expect(result.json).toMatchObject({ reject: true, status: 403 });
  });
});

describe("twilio voice", () => {
  const workflow = loadWorkflow("twilio-voice.json");

  function signedItem(params: Record<string, string>): Item {
    return {
      json: {
        headers: { "x-twilio-signature": twilioSignature(params, "twilio-voice") },
        body: params,
      },
    };
  }

  test("normalizes a missed call", () => {
    const params = {
      CallSid: "CAfake00000000000000000000000001",
      From: "+15550100002",
      To: "+15550209999",
      CallStatus: "no-answer",
    };
    const [result] = runCodeNode(workflow, "Normalize", [signedItem(params)]);
    expect(result.json.reject).toBe(false);
    expect(result.json.event).toMatchObject({
      channel: "phone_call",
      event_type: "missed_call",
      sender_phone: "+15550100002",
    });
    expect(String(result.json.event.content)).toContain("no-answer");
  });

  test("normalizes a completed call with a distinct idempotency key", () => {
    const base = { CallSid: "CAfake2", From: "+15550100002", To: "+15550209999" };
    const [missed] = runCodeNode(workflow, "Normalize", [
      signedItem({ ...base, CallStatus: "no-answer" }),
    ]);
    const [completed] = runCodeNode(workflow, "Normalize", [
      signedItem({ ...base, CallStatus: "completed", CallDuration: "180" }),
    ]);
    expect(completed.json.event.event_type).toBe("call_completed");
    expect(completed.json.idempotency_key).not.toBe(missed.json.idempotency_key);
  });

  test("records voicemail as a reference without storing the recording", () => {
    const params = {
      CallSid: "CAfake3",
      From: "+15550100003",
      CallStatus: "completed",
      RecordingSid: "REfake0001",
      RecordingDuration: "22",
      RecordingUrl: "https://api.twilio.com/recordings/REfake0001",
    };
    const [result] = runCodeNode(workflow, "Normalize", [signedItem(params)]);
    expect(result.json.event.event_type).toBe("voicemail");
    expect(String(result.json.event.content)).toContain("REfake0001");
    expect(JSON.stringify(result.json.event)).not.toContain("https://api.twilio.com");
  });

  test("captures voicemail transcriptions", () => {
    const params = {
      CallSid: "CAfake4",
      From: "+15550100004",
      TranscriptionSid: "TRfake0001",
      TranscriptionText: "Hi, call me back about the estimate.",
    };
    const [result] = runCodeNode(workflow, "Normalize", [signedItem(params)]);
    expect(result.json.event.event_type).toBe("voicemail_transcription");
    expect(String(result.json.event.content)).toContain("call me back");
  });

  test("rejects a forged signature", () => {
    const item = {
      json: { headers: { "x-twilio-signature": "forged" }, body: { CallSid: "CAx" } },
    };
    const [result] = runCodeNode(workflow, "Normalize", [item]);
    expect(result.json).toMatchObject({ reject: true, status: 403 });
  });
});

describe("whatsapp", () => {
  const workflow = loadWorkflow("meta-whatsapp.json");
  const payload = {
    object: "whatsapp_business_account",
    entry: [
      {
        id: "waba-1",
        changes: [
          {
            field: "messages",
            value: {
              metadata: { phone_number_id: "pn-1", display_phone_number: "15550209999" },
              contacts: [{ wa_id: "15550106000", profile: { name: "WA Customer" } }],
              messages: [
                {
                  id: "wamid.fake001",
                  from: "15550106000",
                  timestamp: "1770000000",
                  type: "text",
                  text: { body: "Hola, necesito una cotización" },
                },
                {
                  id: "wamid.fake002",
                  from: "15550106000",
                  timestamp: "1770000060",
                  type: "image",
                  image: { id: "media-1" },
                },
              ],
            },
          },
        ],
      },
    ],
  };

  test("normalizes batched messages, preserving the stable sender ID", () => {
    const results = runCodeNode(workflow, "Normalize", [metaItem(payload)]);
    expect(results).toHaveLength(2);
    expect(results[0].json.idempotency_key).toBe("meta:whatsapp:wamid.fake001");
    expect(results[0].json.event).toMatchObject({
      channel: "whatsapp",
      provider: "meta",
      external_sender_id: "15550106000",
      sender_phone: "+15550106000",
      sender_name: "WA Customer",
      content: "Hola, necesito una cotización",
    });
    // Unsupported media becomes a descriptive activity, not stored media.
    expect(results[1].json.event?.event_type).toBe("media_message");
    expect(String(results[1].json.event?.content)).toContain("image");
  });

  test("rejects invalid signatures", () => {
    const [result] = runCodeNode(workflow, "Normalize", [metaItem(payload, false)]);
    expect(result.json).toMatchObject({ reject: true, status: 403 });
  });

  test("acknowledges status-only payloads without a CRM write", () => {
    const statuses = {
      object: "whatsapp_business_account",
      entry: [{ id: "waba-1", changes: [{ field: "messages", value: { statuses: [{}] } }] }],
    };
    const [result] = runCodeNode(workflow, "Normalize", [metaItem(statuses)]);
    expect(result.json).toMatchObject({ reject: true, status: 200 });
  });

  test("verifies subscriptions only with the configured token", () => {
    const good = runCodeNode(workflow, "Verify Subscription", [
      {
        json: {
          query: {
            "hub.mode": "subscribe",
            "hub.verify_token": ENV.META_VERIFY_TOKEN,
            "hub.challenge": "challenge-123",
          },
        },
      },
    ]);
    expect(good[0].json).toMatchObject({ reject: false, challenge: "challenge-123" });
    const bad = runCodeNode(workflow, "Verify Subscription", [
      { json: { query: { "hub.mode": "subscribe", "hub.verify_token": "wrong" } } },
    ]);
    expect(bad[0].json).toMatchObject({ reject: true, status: 403 });
  });
});

describe("facebook messenger", () => {
  const workflow = loadWorkflow("meta-messenger.json");
  const payload = {
    object: "page",
    entry: [
      {
        id: "page-1",
        time: 1770000000000,
        messaging: [
          {
            sender: { id: "psid-90001" },
            recipient: { id: "page-1" },
            timestamp: 1770000000000,
            message: { mid: "mid.fake001", text: "Saw your page, need HVAC help" },
          },
          {
            sender: { id: "psid-90001" },
            recipient: { id: "page-1" },
            timestamp: 1770000060000,
            message: { mid: "mid.fake002", attachments: [{ type: "image", payload: {} }] },
          },
          { sender: { id: "page-1" }, timestamp: 1770000070000, delivery: { mids: [] } },
        ],
      },
    ],
  };

  test("normalizes batched messages and skips non-message entries", () => {
    const results = runCodeNode(workflow, "Normalize", [metaItem(payload)]);
    expect(results).toHaveLength(2);
    expect(results[0].json.idempotency_key).toBe("meta:facebook:mid.fake001");
    expect(results[0].json.event).toMatchObject({
      channel: "facebook",
      external_sender_id: "psid-90001",
      content: "Saw your page, need HVAC help",
      metadata: { page_id: "page-1" },
    });
    expect(String(results[1].json.event?.content)).toContain("image");
  });

  test("rejects invalid signatures", () => {
    const [result] = runCodeNode(workflow, "Normalize", [metaItem(payload, false)]);
    expect(result.json).toMatchObject({ reject: true, status: 403 });
  });
});

describe("workflow hygiene", () => {
  const files = fs.readdirSync(WORKFLOWS_DIR).filter((file) => file.endsWith(".json"));

  test("covers every expected workflow", () => {
    expect(files.sort()).toEqual([
      "error-handler.json",
      "meta-messenger.json",
      "meta-whatsapp.json",
      "twilio-sms.json",
      "twilio-voice.json",
      "website-form-intake.json",
    ]);
  });

  test("contains no embedded credentials or secrets", () => {
    for (const file of files) {
      const content = fs.readFileSync(path.join(WORKFLOWS_DIR, file), "utf8");
      expect(content, file).not.toMatch(/"credentials"/);
      expect(content, file).not.toMatch(/Bearer\s+[A-Za-z0-9]/);
      expect(content, file).not.toMatch(/AC[0-9a-f]{32}/); // Twilio account SIDs
      expect(content, file).not.toMatch(/EAA[A-Za-z0-9]{20,}/); // Meta tokens
      expect(content, file).not.toMatch(/[0-9a-f]{48,}/); // raw key material
    }
  });

  test("never writes to PostgreSQL directly and calls the CRM via env config", () => {
    for (const file of files) {
      const workflow = loadWorkflow(file);
      for (const node of workflow.nodes) {
        expect(node.type, `${file}:${node.name}`).not.toMatch(/postgres|mysql|database/i);
      }
      if (file === "error-handler.json") continue;
      const crm = workflow.nodes.find((node) => node.name === "CRM Write");
      expect(crm, file).toBeDefined();
      expect(crm!.parameters.url).toBe("={{ $env.CRM_API_URL }}/api/v1/inbound/events");
      expect(crm!.retryOnFail).toBe(true);
      expect(crm!.maxTries).toBeLessThanOrEqual(5); // bounded, never indefinite
      const headers = JSON.stringify(crm!.parameters.headerParameters);
      expect(headers).toContain("$env.CRM_INBOUND_API_KEY"); // key from env, not embedded
    }
  });
});
