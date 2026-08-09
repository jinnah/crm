import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { BrandingSection } from "@/components/settings/branding-section";

const BRANDING = {
  business_name: "Acme Roofing",
  has_logo: false,
  width: null,
  height: null,
  updated_at: null,
  initials: "AR",
};

const WITH_LOGO = {
  ...BRANDING,
  has_logo: true,
  width: 200,
  height: 100,
  updated_at: "2026-08-09T12:00:00Z",
};

function stubBranding(
  initial: unknown,
  mutation: { status: number; body: unknown } = { status: 200, body: WITH_LOGO },
) {
  const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
    const init = args[1] as RequestInit | undefined;
    const answer =
      init?.method === "POST" || init?.method === "DELETE"
        ? mutation
        : { status: 200, body: initial };
    return Promise.resolve({
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json: () => Promise.resolve(answer.body),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:preview"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("without a logo, the wordmark fallback and requirements are shown", async () => {
  stubBranding(BRANDING);
  render(<BrandingSection csrfToken="csrf" />);
  expect(await screen.findByText("No logo yet")).toBeInTheDocument();
  expect(screen.getAllByText("AR").length).toBeGreaterThan(0); // initials, not a broken image
  expect(screen.getByText(/PNG, JPEG or WebP · up to 1 MB/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Remove logo/ })).not.toBeInTheDocument();
});

test("choosing a file previews it and nothing is uploaded until save", async () => {
  const fetchMock = stubBranding(BRANDING);
  render(<BrandingSection csrfToken="csrf" />);
  await screen.findByText("No logo yet");

  const file = new File([new Uint8Array(64)], "logo.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Choose a logo file"), {
    target: { files: [file] },
  });

  expect(await screen.findByText(/not saved yet/)).toBeInTheDocument();
  expect(screen.getByAltText("Logo preview")).toHaveAttribute("src", "blob:preview");
  expect(
    fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST"),
  ).toHaveLength(0);

  // Saving posts the raw image body with its content type and the CSRF token.
  fireEvent.click(screen.getByRole("button", { name: "Save logo" }));
  await waitFor(() => {
    const posts = fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    );
    expect(posts).toHaveLength(1);
    const [url, init] = posts[0];
    expect(String(url)).toContain("/settings/branding/logo");
    expect((init as RequestInit).headers).toMatchObject({
      "Content-Type": "image/png",
      "X-CSRF-Token": "csrf",
    });
    expect((init as RequestInit).body).toBe(file);
  });
  expect(await screen.findByText(/Logo saved/)).toBeInTheDocument();
});

test("a wrong file type and an oversized file are refused before any upload", async () => {
  const fetchMock = stubBranding(BRANDING);
  render(<BrandingSection csrfToken="csrf" />);
  await screen.findByText("No logo yet");
  const input = screen.getByLabelText("Choose a logo file");

  fireEvent.change(input, {
    target: { files: [new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" })] },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PNG, JPEG or WebP image.");

  const big = new File([new Uint8Array(1024 * 1024 + 1)], "big.png", { type: "image/png" });
  fireEvent.change(input, { target: { files: [big] } });
  expect(await screen.findByRole("alert")).toHaveTextContent("1 MB or smaller");

  expect(
    fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST"),
  ).toHaveLength(0);
});

test("a server rejection is shown without losing the page", async () => {
  stubBranding(BRANDING, {
    status: 400,
    body: { detail: "That file is not a readable PNG, JPEG or WebP image." },
  });
  render(<BrandingSection csrfToken="csrf" />);
  await screen.findByText("No logo yet");
  fireEvent.change(screen.getByLabelText("Choose a logo file"), {
    target: { files: [new File([new Uint8Array(64)], "fake.png", { type: "image/png" })] },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Save logo" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not a readable");
});

test("removing the logo asks for confirmation first", async () => {
  const fetchMock = stubBranding(WITH_LOGO, { status: 200, body: BRANDING });
  render(<BrandingSection csrfToken="csrf" />);
  expect(await screen.findByText("Current logo")).toBeInTheDocument();
  expect(screen.getByText(/200×100px/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Remove logo/ }));
  expect(await screen.findByRole("dialog", { name: "Remove the logo?" })).toBeInTheDocument();
  // Nothing happens until it is confirmed.
  expect(
    fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === "DELETE",
    ),
  ).toHaveLength(0);

  const dialog = screen.getByRole("dialog", { name: "Remove the logo?" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove logo" }));
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toHaveLength(1);
  });
  expect(await screen.findByText(/Logo removed/)).toBeInTheDocument();
});
