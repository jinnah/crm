import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import PublicRequestPage from "./page";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(submitResponse: { status: number; body: unknown }) {
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const url = String(args[0]);
    if (url.includes("/public/form-info")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            form_title: "Request a quote",
            form_intro: "Tell us what you need.",
            business_name: "Acme Roofing",
          }),
      });
    }
    return Promise.resolve({
      ok: submitResponse.status >= 200 && submitResponse.status < 300,
      status: submitResponse.status,
      json: () => Promise.resolve(submitResponse.body),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function fill(values: { name?: string; email?: string; phone?: string; message?: string }) {
  if (values.name !== undefined)
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: values.name } });
  if (values.email !== undefined)
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: values.email } });
  if (values.phone !== undefined)
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: values.phone } });
  if (values.message !== undefined)
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: values.message } });
}

test("renders accessible labeled fields and the configured title", async () => {
  stubFetch({ status: 200, body: { status: "ok" } });
  render(<PublicRequestPage />);
  expect(await screen.findByRole("heading", { name: "Request a quote" })).toBeInTheDocument();
  expect(screen.getByText("Tell us what you need.")).toBeInTheDocument();
  for (const label of ["Name", "Email", "Phone", "Message"]) {
    expect(screen.getByLabelText(label)).toBeInTheDocument();
  }
  expect(screen.getByRole("button", { name: "Send request" })).toBeInTheDocument();
});

test("requires a message and at least one contact method", async () => {
  const fetchMock = stubFetch({ status: 200, body: { status: "ok" } });
  render(<PublicRequestPage />);
  fireEvent.click(screen.getByRole("button", { name: "Send request" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Please tell us what you need.");

  fill({ message: "My roof leaks" });
  fireEvent.click(screen.getByRole("button", { name: "Send request" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Please provide an email address or a phone number",
  );
  const submits = fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("/api/public-request"),
  );
  expect(submits).toHaveLength(0);
});

test("submits same-origin with a stable submission id and shows success", async () => {
  const fetchMock = stubFetch({ status: 200, body: { status: "ok", duplicate: false } });
  render(<PublicRequestPage />);
  await screen.findByRole("heading", { name: "Request a quote" });
  fill({ name: "Pat", email: "pat@example.com", message: "My roof leaks" });
  fireEvent.click(screen.getByRole("button", { name: "Send request" }));

  expect(await screen.findByRole("status")).toHaveTextContent("Acme Roofing");
  const submit = fetchMock.mock.calls.find(([url]) =>
    String(url).includes("/api/public-request"),
  );
  expect(submit).toBeDefined();
  // Same-origin relative path: the browser never talks to n8n or the CRM key.
  expect(String(submit![0])).toBe("/api/public-request");
  const body = JSON.parse(String((submit![1] as RequestInit).body));
  expect(body.submission_id).toBeTruthy();
  expect(body.message).toBe("My roof leaks");
});

test("reports a duplicate submission distinctly", async () => {
  stubFetch({ status: 200, body: { status: "ok", duplicate: true } });
  render(<PublicRequestPage />);
  await screen.findByRole("heading", { name: "Request a quote" });
  fill({ phone: "+15550100001", message: "Same request again" });
  fireEvent.click(screen.getByRole("button", { name: "Send request" }));
  expect(
    await screen.findByRole("heading", { name: "We already have your request" }),
  ).toBeInTheDocument();
});

test("shows a failure message when the server rejects the request", async () => {
  stubFetch({ status: 502, body: { error: "We could not submit your request." } });
  render(<PublicRequestPage />);
  await screen.findByRole("heading", { name: "Request a quote" });
  fill({ email: "pat@example.com", message: "Trying again" });
  fireEvent.click(screen.getByRole("button", { name: "Send request" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("We could not submit your request."),
  );
});

test("keeps no secrets in the client bundle source", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const source = fs.readFileSync(path.resolve(__dirname, "page.tsx"), "utf8");
  expect(source).not.toMatch(/FORM_SHARED_SECRET/);
  expect(source).not.toMatch(/INBOUND_API_KEY/);
  expect(source).not.toMatch(/TWILIO/);
});
