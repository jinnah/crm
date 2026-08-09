import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import LoginPage from "./page";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}));

beforeEach(() => {
  replace.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "owner@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "correct horse battery staple" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

test("renders accessible labeled fields", () => {
  render(<LoginPage />);
  expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
  expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
  expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Forgot password?" })).toBeInTheDocument();
});

test("shows the generic error on failed login", async () => {
  stubFetchRoutes([["/auth/login", { status: 401, body: { detail: "Invalid email or password." } }]]);
  render(<LoginPage />);
  fillAndSubmit();
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
  expect(replace).not.toHaveBeenCalled();
});

test("submits credentials with cookies and redirects to the shell", async () => {
  const fetchMock = stubFetchRoutes([
    ["/auth/login", { status: 200, body: { user: makeUser(), csrf_token: "csrf-token" } }],
  ]);
  render(<LoginPage />);
  fillAndSubmit();
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/auth/login"),
    expect.objectContaining({ method: "POST", credentials: "include" }),
  );
});

test("redirects to the forced password change when required", async () => {
  stubFetchRoutes([
    [
      "/auth/login",
      {
        status: 200,
        body: { user: makeUser({ must_change_password: true }), csrf_token: "csrf-token" },
      },
    ],
  ]);
  render(<LoginPage />);
  fillAndSubmit();
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/change-password"));
});
