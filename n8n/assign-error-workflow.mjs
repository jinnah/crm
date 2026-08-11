#!/usr/bin/env node
/**
 * Assign and verify the error workflow on every production n8n workflow.
 *
 * Workflow IDs are installation-specific, so the committed workflow files
 * cannot carry the assignment. After every `install-workflows.sh` run (and
 * before activation), run:
 *
 *     node n8n/assign-error-workflow.mjs check   # read-only validation
 *     node n8n/assign-error-workflow.mjs apply   # assign where missing
 *
 * Configuration comes from the environment only — nothing installation-
 * specific lives in Git:
 *
 *     N8N_API_BASE_URL  e.g. http://127.0.0.1:5678
 *     N8N_API_KEY       an n8n API key (editor: Settings -> n8n API)
 *
 * The script uses the official public REST API (GET/PUT /api/v1/workflows,
 * X-N8N-API-KEY): it discovers the installed "Inbound Error Handler" and
 * every workflow named in the committed n8n/workflows/*.json files, resolves
 * both by EXACT name (zero or duplicate matches fail), updates only
 * `settings.errorWorkflow` while resending the workflow's own name, nodes,
 * connections and settings unchanged, then re-reads each workflow and
 * verifies the persisted value — a write is never trusted on its own.
 * Already-correct workflows are not written at all, so re-running is
 * idempotent. Activation state is never touched (n8n re-publishes a
 * published workflow on update by itself). Exit codes: 0 = verified
 * correct, 1 = validation/assignment failure, 2 = usage error.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ERROR_HANDLER_NAME = "Inbound Error Handler";
const PAGE_LIMIT = 250;

/** Names of every committed workflow: the handler plus the assignment
 * targets. The committed files are the single source of truth, so a new
 * workflow added to n8n/workflows/ is covered automatically. */
export function expectedWorkflowNames(workflowsDir) {
  const names = [];
  for (const file of fs.readdirSync(workflowsDir).sort()) {
    if (!file.endsWith(".json")) continue;
    const parsed = JSON.parse(fs.readFileSync(path.join(workflowsDir, file), "utf8"));
    const documents = Array.isArray(parsed) ? parsed : [parsed];
    for (const workflow of documents) {
      if (typeof workflow?.name !== "string" || !workflow.name) {
        throw new Error(`${file}: committed workflow is missing a name`);
      }
      names.push(workflow.name);
    }
  }
  const duplicates = names.filter((name, index) => names.indexOf(name) !== index);
  if (duplicates.length) {
    throw new Error(`duplicate committed workflow names: ${[...new Set(duplicates)].join(", ")}`);
  }
  if (!names.includes(ERROR_HANDLER_NAME)) {
    throw new Error(`no committed workflow is named "${ERROR_HANDLER_NAME}"`);
  }
  return {
    handler: ERROR_HANDLER_NAME,
    targets: names.filter((name) => name !== ERROR_HANDLER_NAME),
  };
}

/** True when the fetched handler document actually contains an Error
 * Trigger node — anything else cannot serve as an error workflow. */
export function isErrorHandlerShaped(workflow) {
  return (
    Array.isArray(workflow?.nodes) &&
    workflow.nodes.some((node) => node?.type === "n8n-nodes-base.errorTrigger")
  );
}

class ApiError extends Error {}

/**
 * Run the assignment/validation.
 *
 * mode: "check" (read-only) or "apply".
 * fetchImpl/log are injectable for tests. Returns the process exit code;
 * never throws for operational failures and never prints the API key.
 */
export async function runAssignment({
  mode,
  baseUrl,
  apiKey,
  workflowsDir,
  fetchImpl = globalThis.fetch,
  log = console.log,
}) {
  if (mode !== "check" && mode !== "apply") {
    log(`usage: assign-error-workflow.mjs check|apply (got "${mode ?? ""}")`);
    return 2;
  }
  if (!baseUrl || !apiKey) {
    log("N8N_API_BASE_URL and N8N_API_KEY must be set (never committed).");
    return 2;
  }
  const api = baseUrl.replace(/\/+$/, "") + "/api/v1";
  const headers = { "X-N8N-API-KEY": apiKey, accept: "application/json" };

  async function request(pathname, options = {}) {
    let response;
    try {
      response = await fetchImpl(api + pathname, { ...options, headers: { ...headers, ...options.headers } });
    } catch (error) {
      // Sanitized: the message of a network error never includes headers.
      throw new ApiError(`request to ${pathname} failed: ${error?.message ?? "network error"}`);
    }
    if (response.status === 401 || response.status === 403) {
      throw new ApiError(`n8n rejected the API key for ${pathname} (HTTP ${response.status})`);
    }
    if (!response.ok) {
      throw new ApiError(`n8n returned HTTP ${response.status} for ${pathname}`);
    }
    try {
      return await response.json();
    } catch {
      throw new ApiError(`n8n returned a non-JSON response for ${pathname}`);
    }
  }

  async function listAllWorkflows() {
    const all = [];
    let cursor;
    do {
      const query = cursor
        ? `?limit=${PAGE_LIMIT}&cursor=${encodeURIComponent(cursor)}`
        : `?limit=${PAGE_LIMIT}`;
      const page = await request(`/workflows${query}`);
      if (!Array.isArray(page?.data)) {
        throw new ApiError("workflow list response is malformed (no data array)");
      }
      all.push(...page.data);
      cursor = page.nextCursor ?? null;
    } while (cursor);
    return all;
  }

  const failures = [];
  const fail = (message) => {
    failures.push(message);
    log(`FAIL  ${message}`);
  };

  let expected;
  try {
    expected = expectedWorkflowNames(workflowsDir);
  } catch (error) {
    log(`FAIL  ${error.message}`);
    return 1;
  }

  let installed;
  try {
    installed = await listAllWorkflows();
  } catch (error) {
    log(`FAIL  ${error.message}`);
    return 1;
  }

  function resolveByName(name) {
    const matches = installed.filter((workflow) => workflow?.name === name);
    if (matches.length === 0) {
      fail(`workflow "${name}" is not installed`);
      return null;
    }
    if (matches.length > 1) {
      fail(`workflow "${name}" is installed ${matches.length} times — remove the duplicates first`);
      return null;
    }
    if (typeof matches[0]?.id !== "string" || !matches[0].id) {
      fail(`workflow "${name}" has no usable id in the list response`);
      return null;
    }
    return matches[0];
  }

  const handler = resolveByName(expected.handler);
  if (handler === null) return 1;

  try {
    const handlerDetail = await request(`/workflows/${encodeURIComponent(handler.id)}`);
    if (!isErrorHandlerShaped(handlerDetail)) {
      fail(`"${expected.handler}" has no Error Trigger node — it cannot be the error workflow`);
      return 1;
    }
  } catch (error) {
    log(`FAIL  ${error.message}`);
    return 1;
  }

  for (const name of expected.targets) {
    const listed = resolveByName(name);
    if (listed === null) continue;
    if (listed.id === handler.id) {
      fail(`"${name}" resolves to the error handler itself — refusing self-assignment`);
      continue;
    }
    try {
      const detail = await request(`/workflows/${encodeURIComponent(listed.id)}`);
      if (
        typeof detail?.name !== "string" ||
        !Array.isArray(detail?.nodes) ||
        typeof detail?.connections !== "object" ||
        detail.connections === null ||
        typeof detail?.settings !== "object" ||
        detail.settings === null
      ) {
        fail(`"${name}" returned a malformed workflow document`);
        continue;
      }
      if (detail.settings.errorWorkflow === handler.id) {
        log(`ok    ${name}`);
        continue;
      }
      if (mode === "check") {
        fail(`"${name}" does not reference the error handler (errorWorkflow=${detail.settings.errorWorkflow ?? "unset"})`);
        continue;
      }
      // Apply: resend the workflow's own document with ONLY the error
      // workflow changed. Read-only fields (id, active, tags, ...) are not
      // part of the update contract and are deliberately absent.
      const payload = {
        name: detail.name,
        nodes: detail.nodes,
        connections: detail.connections,
        settings: { ...detail.settings, errorWorkflow: handler.id },
      };
      if (detail.staticData !== undefined && detail.staticData !== null) {
        payload.staticData = detail.staticData;
      }
      await request(`/workflows/${encodeURIComponent(listed.id)}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      // Never trust the write: re-read and verify what actually persisted.
      const persisted = await request(`/workflows/${encodeURIComponent(listed.id)}`);
      if (persisted?.settings?.errorWorkflow !== handler.id) {
        fail(`"${name}" did not persist the error workflow after update`);
        continue;
      }
      log(`assigned  ${name}`);
    } catch (error) {
      fail(error instanceof ApiError ? error.message : `"${name}": ${error?.message ?? "unexpected error"}`);
    }
  }

  if (failures.length) {
    log(`${failures.length} workflow(s) failed ${mode}.`);
    return 1;
  }
  log(`All ${expected.targets.length} workflows reference "${expected.handler}".`);
  return 0;
}

const invokedDirectly =
  process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);

if (invokedDirectly) {
  const exitCode = await runAssignment({
    mode: process.argv[2],
    baseUrl: process.env.N8N_API_BASE_URL,
    apiKey: process.env.N8N_API_KEY,
    workflowsDir: path.join(path.dirname(fileURLToPath(import.meta.url)), "workflows"),
  });
  process.exit(exitCode);
}
