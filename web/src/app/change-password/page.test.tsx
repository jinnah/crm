import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ChangePasswordPage from "./page";

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

test("redirects unauthenticated users to login", async () => {
  stubFetchRoutes([["/auth/session", { status: 401, body: { detail: "Not authenticated." } }]]);
  render(<ChangePasswordPage />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
});

test("redirects users without a pending change to the shell", async () => {
  stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser(), csrf_token: "csrf" } }],
  ]);
  render(<ChangePasswordPage />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
});

test("forces the flagged user through the change flow, then enters the shell", async () => {
  const fetchMock = stubFetchRoutes([
    [
      "/auth/session",
      {
        status: 200,
        body: { user: makeUser({ must_change_password: true }), csrf_token: "csrf-token" },
      },
    ],
    ["/auth/change-password", { status: 204, body: null }],
  ]);
  render(<ChangePasswordPage />);

  expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Current password"), {
    target: { value: "temporary password 123" },
  });
  fireEvent.change(screen.getByLabelText("New password"), {
    target: { value: "my real chosen password" },
  });
  fireEvent.change(screen.getByLabelText("Confirm new password"), {
    target: { value: "my real chosen password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Set new password" }));

  await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  const changeCall = fetchMock.mock.calls.find(([url]) =>
    String(url).includes("/auth/change-password"),
  );
  expect(changeCall).toBeDefined();
  expect((changeCall![1] as RequestInit).headers).toMatchObject({
    "X-CSRF-Token": "csrf-token",
  });
});

function stubForcedSession(logoutStatus: number) {
  return stubFetchRoutes([
    [
      "/auth/session",
      {
        status: 200,
        body: { user: makeUser({ must_change_password: true }), csrf_token: "csrf-token" },
      },
    ],
    ["/auth/logout", { status: logoutStatus, body: logoutStatus === 204 ? null : {} }],
  ]);
}

test("a forced-change user can log out", async () => {
  const fetchMock = stubForcedSession(204);
  render(<ChangePasswordPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Log out" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  const logoutCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/auth/logout"));
  expect(logoutCall).toBeDefined();
  expect((logoutCall![1] as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "csrf-token" });
});

test("a failed logout on the forced-change page shows an error and stays", async () => {
  stubForcedSession(500);
  render(<ChangePasswordPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Log out" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unable to log out");
  expect(replace).not.toHaveBeenCalledWith("/login");
  expect(screen.getByRole("button", { name: "Log out" })).toBeEnabled();
});
