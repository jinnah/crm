import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { stubFetchRoutes } from "@/test/helpers";
import ResetPasswordPage from "./page";

const { searchParams } = vi.hoisted(() => ({
  searchParams: { value: new URLSearchParams("token=raw-reset-token") },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => searchParams.value,
}));

afterEach(() => {
  vi.unstubAllGlobals();
  searchParams.value = new URLSearchParams("token=raw-reset-token");
});

function fill(newPassword: string, confirmation: string) {
  fireEvent.change(screen.getByLabelText("New password"), {
    target: { value: newPassword },
  });
  fireEvent.change(screen.getByLabelText("Confirm new password"), {
    target: { value: confirmation },
  });
  fireEvent.click(screen.getByRole("button", { name: "Reset password" }));
}

test("rejects a password below the minimum length without calling the API", async () => {
  const fetchMock = stubFetchRoutes([]);
  render(<ResetPasswordPage />);
  fill("short", "short");
  expect(await screen.findByRole("alert")).toHaveTextContent("at least 12 characters");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("rejects a mismatched confirmation without calling the API", async () => {
  const fetchMock = stubFetchRoutes([]);
  render(<ResetPasswordPage />);
  fill("a valid new password", "a different password");
  expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("shows the API error for an invalid token", async () => {
  stubFetchRoutes([
    ["/auth/reset-password", { status: 400, body: { detail: "Invalid or expired reset link." } }],
  ]);
  render(<ResetPasswordPage />);
  fill("a valid new password", "a valid new password");
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid or expired reset link.");
});

test("confirms success and links to sign in", async () => {
  const fetchMock = stubFetchRoutes([["/auth/reset-password", { status: 204, body: null }]]);
  render(<ResetPasswordPage />);
  fill("a valid new password", "a valid new password");
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent("Your password has been reset."),
  );
  expect(screen.getByRole("link", { name: "Go to sign in" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/auth/reset-password"),
    expect.objectContaining({ method: "POST" }),
  );
});

test("removes the token from the URL but still submits it", async () => {
  const replaceState = vi.spyOn(window.history, "replaceState");
  const fetchMock = stubFetchRoutes([["/auth/reset-password", { status: 204, body: null }]]);
  render(<ResetPasswordPage />);

  // The token is stripped from the visible URL as soon as the page initializes…
  await waitFor(() =>
    expect(replaceState).toHaveBeenCalledWith(
      window.history.state,
      "",
      window.location.pathname,
    ),
  );

  // …while the captured in-memory token is still submitted to the API.
  fill("a valid new password", "a valid new password");
  await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
  const resetCall = fetchMock.mock.calls.find(([url]) =>
    String(url).includes("/auth/reset-password"),
  );
  expect(resetCall).toBeDefined();
  expect(JSON.parse(String(resetCall![1]!.body))).toMatchObject({ token: "raw-reset-token" });
  replaceState.mockRestore();
});

test("does not rewrite history when no token is present", () => {
  searchParams.value = new URLSearchParams("");
  const replaceState = vi.spyOn(window.history, "replaceState");
  stubFetchRoutes([]);
  render(<ResetPasswordPage />);
  expect(replaceState).not.toHaveBeenCalled();
  replaceState.mockRestore();
});

test("shows an error when the link has no token", () => {
  searchParams.value = new URLSearchParams("");
  stubFetchRoutes([]);
  render(<ResetPasswordPage />);
  expect(screen.getByRole("alert")).toHaveTextContent("This reset link is invalid.");
});
