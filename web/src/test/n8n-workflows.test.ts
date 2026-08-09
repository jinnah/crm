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
  CRM_SEND_SECRET: "test-crm-send-secret-not-real",
  TWILIO_ACCOUNT_SID: "ACfake00000000000000000000000000",
  TWILIO_FROM_NUMBER: "+15550209999",
};

type Item = { json: Record<string, unknown>; binary?: Record<string, unknown> };
type Workflow = {
  name: string;
  settings?: Record<string, unknown>;
  connections?: Record<string, unknown>;
  nodes: Array<{
    name: string;
    type: string;
    parameters: Record<string, unknown>;
    retryOnFail?: boolean;
    maxTries?: number;
    onError?: string;
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
      "twilio-send.json",
      "twilio-sms.json",
      "twilio-status.json",
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
      const crm = workflow.nodes.find((node) => node.name === "CRM Write");
      if (!crm) continue;
      expect(crm.parameters.url).toBe("={{ $env.CRM_API_URL }}/api/v1/inbound/events");
      const headers = JSON.stringify(crm.parameters.headerParameters);
      expect(headers).toContain("$env.CRM_INBOUND_API_KEY"); // key from env, never embedded
      // No blanket retry: the classifier decides what may be retried.
      expect(crm.retryOnFail ?? false).toBe(false);
    }
  });

  test("channel workflows retain no payloads on successful executions", () => {
    for (const file of files) {
      if (file === "error-handler.json") continue;
      const workflow = loadWorkflow(file);
      expect(workflow.settings?.saveDataSuccessExecution, file).toBe("none");
    }
  });

  test("outbound send workflow keeps Twilio credentials out of the export", () => {
    const workflow = loadWorkflow("twilio-send.json");
    const send = workflow.nodes.find((node) => node.name === "Twilio Send");
    expect(send).toBeDefined();
    // Auth is a runtime-computed header from env, never a stored credential.
    expect(JSON.stringify(send!.parameters.headerParameters)).toContain("$json.auth_header");
    const serialized = JSON.stringify(workflow);
    expect(serialized).toContain("$env.TWILIO_ACCOUNT_SID");
    expect(serialized).toContain("$env.TWILIO_AUTH_TOKEN");
    expect(serialized).not.toMatch(/AC[0-9a-f]{32}/);
    expect(serialized).not.toMatch(/"credentials"/);
  });
});

describe("selective CRM retry", () => {
  const workflow = loadWorkflow("website-form-intake.json");

  function classify(statusCode: number, body: unknown = {}) {
    const prepared = {
      json: { event: { channel: "web_form" }, idempotency_key: "form:sub-1" },
    };
    const node = workflow.nodes.find((candidate) => candidate.name === "Classify Response")!;
    const nodeRequire = createRequire(import.meta.url);
    const fn = new Function(
      "$input",
      "$env",
      "$",
      "require",
      "Buffer",
      String(node.parameters.jsCode),
    );
    return fn(
      { all: () => [{ json: { statusCode, body } }] },
      ENV,
      (name: string) => {
        if (name !== "Prepare Attempt") throw new Error(`unexpected node ${name}`);
        return { first: () => prepared };
      },
      nodeRequire,
      Buffer,
    ) as Array<{ json: Record<string, unknown> }>;
  }

  test("2xx succeeds and carries the CRM ids", () => {
    const [result] = classify(200, {
      lead_id: "lead-1",
      activity_id: "activity-1",
      replayed: false,
    });
    expect(result.json).toMatchObject({
      outcome: "success",
      lead_id: "lead-1",
      activity_id: "activity-1",
    });
  });

  test("permanent failures are never retried", () => {
    for (const status of [400, 401, 403, 404, 413, 422]) {
      const [result] = classify(status);
      expect(result.json.outcome, `status ${status}`).toBe("permanent");
    }
  });

  test("temporary failures are routed to the bounded retry node", () => {
    for (const status of [0, 429, 500, 502, 503, 504]) {
      const [result] = classify(status);
      expect(result.json.outcome, `status ${status}`).toBe("retry");
    }
  });

  test("classifier output carries no personal data", () => {
    const [result] = classify(500);
    expect(JSON.stringify(result.json)).not.toMatch(/@example|[+]1555/);
  });

  test("every HTTP node continues on error so a Respond node always runs", () => {
    // Without this, a CRM/provider outage aborts the execution and n8n
    // answers an empty 200 — the caller would think it was accepted.
    for (const file of fs.readdirSync(WORKFLOWS_DIR).filter((f) => f.endsWith(".json"))) {
      const wf = loadWorkflow(file);
      for (const node of wf.nodes) {
        if (node.type !== "n8n-nodes-base.httpRequest") continue;
        expect(node.onError, `${file}:${node.name}`).toBe("continueRegularOutput");
      }
    }
  });

  test("retries run synchronously so the webhook answers after the outcome", () => {
    for (const file of fs.readdirSync(WORKFLOWS_DIR).filter((f) => f.endsWith(".json"))) {
      const wf = loadWorkflow(file);
      // A Wait node would end the execution and answer the provider early.
      expect(wf.nodes.every((node) => node.type !== "n8n-nodes-base.wait"), file).toBe(true);
      const retry = wf.nodes.find((node) => node.name === "CRM Write Retry");
      if (!retry) continue;
      expect(retry.retryOnFail).toBe(true);
      expect(retry.maxTries).toBeLessThanOrEqual(4);
    }
  });

  test("the retry classifier reports success or exhaustion", () => {
    const [ok] = runCodeNode(workflow, "Classify Retry", [
      { json: { statusCode: 200, body: { lead_id: "lead-9", activity_id: "act-9" } } },
    ]);
    expect(ok.json).toMatchObject({ outcome: "success", lead_id: "lead-9" });
    const [dead] = runCodeNode(workflow, "Classify Retry", [{ json: { error: "ECONNREFUSED" } }]);
    expect(dead.json).toMatchObject({ outcome: "exhausted", status: 503 });
  });
});

describe("twilio send workflow", () => {
  const workflow = loadWorkflow("twilio-send.json");

  function prepare(body: Record<string, unknown>, secret = ENV.CRM_SEND_SECRET) {
    return runCodeNode(workflow, "Authorize And Prepare", [
      { json: { headers: { "x-send-secret": secret }, body } },
    ]);
  }

  test("rejects an unauthorized caller", () => {
    const [result] = prepare({ to: "+15550100001", body: "hi" }, "wrong-secret-value-here");
    expect(result.json).toMatchObject({ reject: true, status: 401 });
  });

  test("requires an E.164 destination and a body", () => {
    expect(prepare({ to: "5550100001", body: "hi" })[0].json).toMatchObject({
      reject: true,
      status: 422,
    });
    expect(prepare({ to: "+15550100001", body: "  " })[0].json).toMatchObject({
      reject: true,
      status: 422,
    });
  });

  test("prepares a Twilio send with env-held account details", () => {
    const [result] = prepare({
      message_id: "msg-1",
      to: "+15550100001",
      body: "On our way",
    });
    expect(result.json).toMatchObject({
      reject: false,
      to: "+15550100001",
      body: "On our way",
      from: ENV.TWILIO_FROM_NUMBER,
      account_sid: ENV.TWILIO_ACCOUNT_SID,
    });
    expect(String(result.json.status_callback)).toContain("/webhook/twilio-status");
    // Basic auth is derived from env at runtime, never stored.
    const expected =
      "Basic " +
      Buffer.from(`${ENV.TWILIO_ACCOUNT_SID}:${ENV.TWILIO_AUTH_TOKEN}`).toString("base64");
    expect(result.json.auth_header).toBe(expected);
  });

  test("refuses to send when messaging credentials are unset", () => {
    const workflowEnv = { ...ENV, TWILIO_ACCOUNT_SID: "", TWILIO_AUTH_TOKEN: "" };
    const [result] = runCodeNode(
      workflow,
      "Authorize And Prepare",
      [
        {
          json: {
            headers: { "x-send-secret": ENV.CRM_SEND_SECRET },
            body: { to: "+15550100001", body: "hi" },
          },
        },
      ],
      workflowEnv,
    );
    expect(result.json).toMatchObject({ reject: true, status: 503 });
  });

  test("a 2xx with a SID is a definite success", () => {
    const [ok] = runCodeNode(workflow, "Classify Send", [
      { json: { statusCode: 201, body: { sid: "SMfake001", status: "queued" } } },
    ]);
    expect(ok.json).toMatchObject({ status: "submitted", sid: "SMfake001" });
  });

  test("a parseable 4xx rejection is a definite failure", () => {
    const [failed] = runCodeNode(workflow, "Classify Send", [
      { json: { statusCode: 400, body: { code: 21610, message: "Unsubscribed recipient" } } },
    ]);
    expect(failed.json).toMatchObject({
      status: "failed",
      error_code: "21610",
      error_message: "Unsubscribed recipient",
    });
  });

  test("ambiguous provider outcomes never become 'failed'", () => {
    const ambiguous: Array<[string, Record<string, unknown>]> = [
      ["no response at all (network/timeout)", { error: "ECONNRESET" }],
      ["provider 5xx", { statusCode: 500, body: { message: "server error" } }],
      ["provider 503", { statusCode: 503, body: null }],
      ["2xx without a SID", { statusCode: 201, body: { status: "queued" } }],
      ["2xx with an unreadable body", { statusCode: 200, body: "not-json" }],
      ["unparseable 4xx", { statusCode: 429, body: null }],
    ];
    for (const [label, response] of ambiguous) {
      const [result] = runCodeNode(workflow, "Classify Send", [{ json: response }]);
      expect(result.json.status, label).toBe("unknown");
      expect(String(result.json.error_message ?? "").length, label).toBeLessThanOrEqual(300);
    }
  });

  test("the submission itself is never retried automatically", () => {
    const send = workflow.nodes.find((node) => node.name === "Twilio Send")!;
    expect(send.retryOnFail ?? false).toBe(false);
  });

  test("classification output carries no credentials", () => {
    const [result] = runCodeNode(workflow, "Classify Send", [
      {
        json: {
          statusCode: 401,
          headers: { authorization: "Basic c2VjcmV0OnZhbHVl" },
          body: { code: 20003, message: "Authenticate" },
        },
      },
    ]);
    const serialized = JSON.stringify(result.json);
    expect(serialized).not.toMatch(/Basic /);
    expect(serialized).not.toMatch(/authorization/i);
  });
});

describe("twilio delivery status honesty", () => {
  const workflow = loadWorkflow("twilio-status.json");

  function classifyStatus(response: Record<string, unknown>) {
    const node = workflow.nodes.find((c) => c.name === "Classify Status")!;
    const nodeRequire = createRequire(import.meta.url);
    const fn = new Function("$input", "$env", "$", "require", "Buffer", String(node.parameters.jsCode));
    return fn(
      { all: () => [{ json: response }] },
      ENV,
      () => ({ first: () => ({ json: { payload: { provider_sid: "SMfake1", status: "sent" } } }) }),
      nodeRequire,
      Buffer,
    ) as Array<{ json: Record<string, unknown> }>;
  }

  test("only a CRM 2xx becomes a success", () => {
    const [matched] = classifyStatus({ statusCode: 200, body: { matched: true, status: "delivered" } });
    expect(matched.json.outcome).toBe("success");
    // An authenticated callback for a SID we do not track is handled, not an error.
    const [unmatched] = classifyStatus({ statusCode: 200, body: { matched: false, status: null } });
    expect(unmatched.json.outcome).toBe("success");
  });

  test("permanent CRM rejections are not retried", () => {
    for (const status of [400, 401, 403, 404, 413, 422]) {
      const [result] = classifyStatus({ statusCode: status, body: {} });
      expect(result.json.outcome, `status ${status}`).toBe("permanent");
    }
  });

  test("temporary CRM failures are routed to the bounded retry", () => {
    for (const status of [0, 429, 500, 502, 503, 504]) {
      const [result] = classifyStatus({ statusCode: status, body: {} });
      expect(result.json.outcome, `status ${status}`).toBe("retry");
    }
  });

  test("the retry classifier reports success or honest exhaustion", () => {
    const [ok] = runCodeNode(workflow, "Classify Status Retry", [
      { json: { statusCode: 204, body: {} } },
    ]);
    expect(ok.json.outcome).toBe("success");
    const [dead] = runCodeNode(workflow, "Classify Status Retry", [
      { json: { error: "ECONNREFUSED" } },
    ]);
    expect(dead.json).toMatchObject({ outcome: "exhausted", status: 503 });
  });

  test("no CRM result path reaches the 200 response without classification", () => {
    const successResponders = ["Respond Status OK", "Respond Status Retry Result"];
    for (const [source, wiring] of Object.entries(
      workflow.connections as Record<string, { main: Array<Array<{ node: string }>> }>,
    )) {
      const targets = wiring.main.flat().map((connection) => connection.node);
      if (targets.some((target) => successResponders.includes(target))) {
        // Success responses may only be reached from a classifier/router.
        expect(["Route Outcome", "Classify Status Retry"], source).toContain(source);
      }
    }
    // Neither CRM node may answer the webhook directly.
    for (const crmNode of ["CRM Status Update", "CRM Status Retry"]) {
      const wiring = (workflow.connections as Record<string, { main: Array<Array<{ node: string }>> }>)[
        crmNode
      ];
      const targets = wiring.main.flat().map((connection) => connection.node);
      expect(targets.every((target) => target.startsWith("Classify")), crmNode).toBe(true);
    }
  });

  test("error output exposes no CRM key, headers or callback data", () => {
    const [result] = classifyStatus({
      statusCode: 500,
      headers: { "x-api-key": "test-inbound-key-not-real" },
      body: { detail: "boom" },
    });
    const serialized = JSON.stringify(result.json);
    expect(serialized).not.toContain("test-inbound-key-not-real");
    expect(serialized).not.toMatch(/x-api-key/i);
  });
});

describe("twilio delivery status workflow", () => {
  const workflow = loadWorkflow("twilio-status.json");

  function normalize(params: Record<string, string>, forge = false) {
    const signature = forge ? "forged" : twilioSignature(params, "twilio-status");
    return runCodeNode(workflow, "Normalize", [
      { json: { headers: { "x-twilio-signature": signature }, body: params } },
    ]);
  }

  test("normalizes a signed delivery callback", () => {
    const [result] = normalize({
      MessageSid: "SMfake00000000000000000000000009",
      MessageStatus: "delivered",
    });
    expect(result.json.reject).toBe(false);
    expect(result.json.payload).toMatchObject({
      provider_sid: "SMfake00000000000000000000000009",
      status: "delivered",
    });
  });

  test("carries bounded provider error details on failure", () => {
    const [result] = normalize({
      MessageSid: "SMfake00000000000000000000000010",
      MessageStatus: "undelivered",
      ErrorCode: "30003",
      ErrorMessage: "Unreachable destination handset",
    });
    expect(result.json.payload).toMatchObject({
      status: "undelivered",
      error_code: "30003",
      error_message: "Unreachable destination handset",
    });
  });

  test("rejects a forged signature", () => {
    const [result] = normalize({ MessageSid: "SMx", MessageStatus: "delivered" }, true);
    expect(result.json).toMatchObject({ reject: true, status: 403 });
  });

  test("relays to the CRM, retrying only on the dedicated retry node", () => {
    const first = workflow.nodes.find((node) => node.name === "CRM Status Update")!;
    expect(first.parameters.url).toBe("={{ $env.CRM_API_URL }}/api/v1/inbound/message-status");
    // The first attempt must not retry: the classifier decides what is temporary.
    expect(first.retryOnFail ?? false).toBe(false);
    const retry = workflow.nodes.find((node) => node.name === "CRM Status Retry")!;
    expect(retry.retryOnFail).toBe(true);
    expect(retry.maxTries).toBeLessThanOrEqual(4);
  });
});
