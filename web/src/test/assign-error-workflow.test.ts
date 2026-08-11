/**
 * Tests for n8n/assign-error-workflow.mjs against a small in-memory model of
 * the n8n 2.11.3 public API (GET/PUT /api/v1/workflows). Fixture ids are
 * deliberately fake — installation-specific ids never live in the repo.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, test } from "vitest";

import {
  ERROR_HANDLER_NAME,
  expectedWorkflowNames,
  isErrorHandlerShaped,
  runAssignment,
} from "../../../n8n/assign-error-workflow.mjs";

const REAL_WORKFLOWS_DIR = path.resolve(__dirname, "../../../n8n/workflows");

type FakeWorkflow = {
  id: string;
  name: string;
  active?: boolean;
  nodes: Array<Record<string, unknown>>;
  connections: Record<string, unknown>;
  settings: Record<string, unknown>;
  staticData?: unknown;
};

const FAKE_KEY = "test-n8n-api-key-not-real";

function handlerFixture(id = "wf-handler-1"): FakeWorkflow {
  return {
    id,
    name: ERROR_HANDLER_NAME,
    active: false,
    nodes: [{ name: "On Error", type: "n8n-nodes-base.errorTrigger", parameters: {} }],
    connections: {},
    settings: { executionOrder: "v1" },
  };
}

function targetFixture(id: string, name: string, extra: Partial<FakeWorkflow> = {}): FakeWorkflow {
  return {
    id,
    name,
    active: true,
    nodes: [{ name: "Webhook", type: "n8n-nodes-base.webhook", parameters: { path: "x" } }],
    connections: { Webhook: { main: [[]] } },
    settings: { executionOrder: "v1", saveDataSuccessExecution: "none" },
    ...extra,
  };
}

/** Minimal in-memory n8n public API. Records every write; supports failure
 * injection and lying about persistence for the re-read tests. */
function fakeN8n(
  workflows: FakeWorkflow[],
  options: {
    failStatus?: number;
    failOn?: (url: string, method: string) => boolean;
    persistWrites?: boolean;
    malformDetailOf?: string;
    networkErrorOn?: (url: string) => boolean;
  } = {},
) {
  const puts: Array<{ id: string; body: Record<string, unknown> }> = [];
  const persistWrites = options.persistWrites ?? true;
  const byId = new Map(workflows.map((workflow) => [workflow.id, workflow]));

  const fetchImpl = async (url: string, init: Record<string, unknown> = {}) => {
    const method = String(init.method ?? "GET").toUpperCase();
    if (options.networkErrorOn?.(url)) throw new Error("socket disconnected");
    if (options.failOn?.(url, method)) {
      return { ok: false, status: options.failStatus ?? 500, json: async () => ({}) };
    }
    const listMatch = url.match(/\/api\/v1\/workflows(\?|$)/);
    const detailMatch = url.match(/\/api\/v1\/workflows\/([^/?]+)$/);
    if (method === "GET" && listMatch) {
      // List from the raw fixture array (not byId) so tests can model two
      // names sharing one id, as the self-assignment guard requires.
      const summaries = workflows.map(({ id, name, active }) => ({ id, name, active }));
      return { ok: true, status: 200, json: async () => ({ data: summaries, nextCursor: null }) };
    }
    if (method === "GET" && detailMatch) {
      const workflow = byId.get(decodeURIComponent(detailMatch[1]));
      if (!workflow) return { ok: false, status: 404, json: async () => ({}) };
      if (options.malformDetailOf === workflow.name) {
        return { ok: true, status: 200, json: async () => ({ id: workflow.id, name: workflow.name }) };
      }
      return { ok: true, status: 200, json: async () => structuredClone(workflow) };
    }
    if (method === "PUT" && detailMatch) {
      const workflow = byId.get(decodeURIComponent(detailMatch[1]));
      if (!workflow) return { ok: false, status: 404, json: async () => ({}) };
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      puts.push({ id: workflow.id, body });
      if (persistWrites) {
        workflow.name = body.name as string;
        workflow.nodes = body.nodes as FakeWorkflow["nodes"];
        workflow.connections = body.connections as FakeWorkflow["connections"];
        workflow.settings = body.settings as FakeWorkflow["settings"];
      }
      return { ok: true, status: 200, json: async () => structuredClone(workflow) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };
  return { fetchImpl, puts, byId };
}

/** A committed-workflows stand-in: temp dir with fixture names so tests do
 * not depend on the full production set. */
const tempDirs: string[] = [];
function fixtureWorkflowsDir(names: string[]): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "crm-wf-fixtures-"));
  tempDirs.push(dir);
  names.forEach((name, index) => {
    fs.writeFileSync(
      path.join(dir, `${index}.json`),
      JSON.stringify({ name, nodes: [], connections: {}, settings: {} }),
    );
  });
  return dir;
}
afterEach(() => {
  while (tempDirs.length) fs.rmSync(tempDirs.pop()!, { recursive: true, force: true });
});

async function run(
  mode: string,
  fake: ReturnType<typeof fakeN8n>,
  workflowsDir: string,
  overrides: Record<string, unknown> = {},
) {
  const lines: string[] = [];
  const exitCode = await runAssignment({
    mode,
    baseUrl: "http://fake-n8n.invalid:5678",
    apiKey: FAKE_KEY,
    workflowsDir,
    fetchImpl: fake.fetchImpl,
    log: (line: string) => lines.push(line),
    ...overrides,
  });
  return { exitCode, lines, output: lines.join("\n") };
}

describe("committed workflow inventory", () => {
  test("discovers the handler and all committed targets", () => {
    const expected = expectedWorkflowNames(REAL_WORKFLOWS_DIR);
    expect(expected.handler).toBe(ERROR_HANDLER_NAME);
    expect(expected.targets).toContain("Document Email");
    expect(expected.targets).toContain("Website Form Intake");
    expect(expected.targets).not.toContain(ERROR_HANDLER_NAME);
    expect(expected.targets.length).toBe(11);
  });

  test("no committed workflow carries an installation-specific errorWorkflow id", () => {
    for (const file of fs.readdirSync(REAL_WORKFLOWS_DIR)) {
      const workflow = JSON.parse(fs.readFileSync(path.join(REAL_WORKFLOWS_DIR, file), "utf8"));
      expect(workflow.settings?.errorWorkflow, file).toBeUndefined();
    }
  });

  test("the committed handler actually contains an Error Trigger node", () => {
    const handler = JSON.parse(
      fs.readFileSync(path.join(REAL_WORKFLOWS_DIR, "error-handler.json"), "utf8"),
    );
    expect(isErrorHandlerShaped(handler)).toBe(true);
    expect(isErrorHandlerShaped({ nodes: [{ type: "n8n-nodes-base.webhook" }] })).toBe(false);
  });
});

describe("apply mode", () => {
  test("assigns the handler id to unassigned workflows and re-reads to verify", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha", "Beta"]);
    const fake = fakeN8n([
      handlerFixture(),
      targetFixture("wf-a1", "Alpha"),
      targetFixture("wf-b1", "Beta"),
    ]);
    const { exitCode, output } = await run("apply", fake, dir);
    expect(exitCode).toBe(0);
    expect(fake.puts.map((put) => put.id).sort()).toEqual(["wf-a1", "wf-b1"]);
    expect(fake.byId.get("wf-a1")!.settings.errorWorkflow).toBe("wf-handler-1");
    expect(output).toContain("assigned  Alpha");
  });

  test("preserves unrelated settings, nodes, connections and staticData; omits read-only fields", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const alpha = targetFixture("wf-a1", "Alpha", { staticData: { cursor: 42 } });
    const fake = fakeN8n([handlerFixture(), alpha]);
    const { exitCode } = await run("apply", fake, dir);
    expect(exitCode).toBe(0);
    const body = fake.puts[0].body as Record<string, unknown>;
    expect(body.settings).toEqual({
      executionOrder: "v1",
      saveDataSuccessExecution: "none",
      errorWorkflow: "wf-handler-1",
    });
    expect(body.nodes).toEqual(alpha.nodes);
    expect(body.connections).toEqual(alpha.connections);
    expect(body.staticData).toEqual({ cursor: 42 });
    // Read-only fields must not be part of the update payload.
    expect(body.id).toBeUndefined();
    expect(body.active).toBeUndefined();
    expect(body.tags).toBeUndefined();
  });

  test("an already-correct assignment produces no write", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([
      handlerFixture(),
      targetFixture("wf-a1", "Alpha", {
        settings: { executionOrder: "v1", errorWorkflow: "wf-handler-1" },
      }),
    ]);
    const { exitCode, output } = await run("apply", fake, dir);
    expect(exitCode).toBe(0);
    expect(fake.puts).toHaveLength(0);
    expect(output).toContain("ok    Alpha");
  });

  test("a write is not trusted until the re-read confirms persistence", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([handlerFixture(), targetFixture("wf-a1", "Alpha")], {
      persistWrites: false, // the API answers 200 but nothing sticks
    });
    const { exitCode, output } = await run("apply", fake, dir);
    expect(fake.puts).toHaveLength(1); // the write happened...
    expect(exitCode).toBe(1); // ...but success was refused
    expect(output).toContain("did not persist");
  });
});

describe("check mode", () => {
  test("passes without any write when every workflow is correct", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([
      handlerFixture(),
      targetFixture("wf-a1", "Alpha", {
        settings: { errorWorkflow: "wf-handler-1" },
      }),
    ]);
    const { exitCode } = await run("check", fake, dir);
    expect(exitCode).toBe(0);
    expect(fake.puts).toHaveLength(0);
  });

  test("fails without any write when an assignment is missing or wrong", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha", "Beta"]);
    const fake = fakeN8n([
      handlerFixture(),
      targetFixture("wf-a1", "Alpha"),
      targetFixture("wf-b1", "Beta", { settings: { errorWorkflow: "wf-stale-9" } }),
    ]);
    const { exitCode, output } = await run("check", fake, dir);
    expect(exitCode).toBe(1);
    expect(fake.puts).toHaveLength(0);
    expect(output).toContain('"Alpha" does not reference');
    expect(output).toContain('"Beta" does not reference');
  });
});

describe("failure modes", () => {
  test("missing handler fails", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([targetFixture("wf-a1", "Alpha")]);
    const { exitCode, output } = await run("check", fake, dir);
    expect(exitCode).toBe(1);
    expect(output).toContain(`"${ERROR_HANDLER_NAME}" is not installed`);
  });

  test("a duplicated handler fails instead of guessing", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([handlerFixture("wf-h1"), handlerFixture("wf-h2"), targetFixture("wf-a1", "Alpha")]);
    const { exitCode, output } = await run("check", fake, dir);
    expect(exitCode).toBe(1);
    expect(output).toContain("installed 2 times");
  });

  test("a missing target fails", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha", "Missing One"]);
    const fake = fakeN8n([handlerFixture(), targetFixture("wf-a1", "Alpha", { settings: { errorWorkflow: "wf-handler-1" } })]);
    const { exitCode, output } = await run("check", fake, dir);
    expect(exitCode).toBe(1);
    expect(output).toContain('"Missing One" is not installed');
  });

  test("a duplicated target fails instead of guessing", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([handlerFixture(), targetFixture("wf-a1", "Alpha"), targetFixture("wf-a2", "Alpha")]);
    const { exitCode, output } = await run("apply", fake, dir);
    expect(exitCode).toBe(1);
    expect(fake.puts).toHaveLength(0);
    expect(output).toContain('"Alpha" is installed 2 times');
  });

  test("a target resolving to the handler itself is refused", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    // Same id listed under both names — assignment would point the handler
    // at itself. The handler doc registers last so the id resolves to a
    // structurally valid handler and the self-assignment guard is what fires.
    const fake = fakeN8n([targetFixture("wf-h1", "Alpha"), handlerFixture("wf-h1")]);
    const { exitCode, output } = await run("apply", fake, dir);
    expect(exitCode).toBe(1);
    expect(fake.puts).toHaveLength(0);
    expect(output).toContain("refusing self-assignment");
  });

  test("a handler without an Error Trigger node is rejected", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const brokenHandler = { ...handlerFixture(), nodes: [{ type: "n8n-nodes-base.noOp" }] };
    const fake = fakeN8n([brokenHandler, targetFixture("wf-a1", "Alpha")]);
    const { exitCode, output } = await run("apply", fake, dir);
    expect(exitCode).toBe(1);
    expect(output).toContain("no Error Trigger node");
  });

  test("a malformed workflow document fails cleanly", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([handlerFixture(), targetFixture("wf-a1", "Alpha")], {
      malformDetailOf: "Alpha",
    });
    const { exitCode, output } = await run("apply", fake, dir);
    expect(exitCode).toBe(1);
    expect(output).toContain("malformed workflow document");
  });

  test("authentication, server and network failures are sanitized", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    for (const options of [
      { failStatus: 401, failOn: () => true },
      { failStatus: 500, failOn: () => true },
      { networkErrorOn: () => true },
    ]) {
      const fake = fakeN8n([handlerFixture(), targetFixture("wf-a1", "Alpha")], options);
      const { exitCode, output } = await run("check", fake, dir);
      expect(exitCode).toBe(1);
      expect(output).not.toContain(FAKE_KEY);
    }
  });

  test("missing configuration or mode is a usage error, not a crash", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME]);
    const fake = fakeN8n([handlerFixture()]);
    expect((await run("frobnicate", fake, dir)).exitCode).toBe(2);
    expect((await run("check", fake, dir, { apiKey: undefined })).exitCode).toBe(2);
  });

  test("output never contains the API key", async () => {
    const dir = fixtureWorkflowsDir([ERROR_HANDLER_NAME, "Alpha"]);
    const fake = fakeN8n([handlerFixture(), targetFixture("wf-a1", "Alpha")]);
    for (const mode of ["check", "apply"]) {
      const { output } = await run(mode, fake, dir);
      expect(output).not.toContain(FAKE_KEY);
    }
  });
});
