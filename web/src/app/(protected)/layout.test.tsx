import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "./layout";

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

test("redirects unauthenticated users to login without rendering content", async () => {
  stubFetchRoutes([["/auth/session", { status: 401, body: { detail: "Not authenticated." } }]]);
  render(
    <ProtectedLayout>
      <p>Secret content</p>
    </ProtectedLayout>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  expect(screen.queryByText("Secret content")).not.toBeInTheDocument();
});

test("redirects users with a pending password change to the forced flow", async () => {
  stubFetchRoutes([
    [
      "/auth/session",
      {
        status: 200,
        body: { user: makeUser({ must_change_password: true }), csrf_token: "csrf" },
      },
    ],
  ]);
  render(
    <ProtectedLayout>
      <p>Secret content</p>
    </ProtectedLayout>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/change-password"));
  expect(screen.queryByText("Secret content")).not.toBeInTheDocument();
});

test("shows the current user, role and owner navigation", async () => {
  stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser(), csrf_token: "csrf" } }],
  ]);
  render(
    <ProtectedLayout>
      <p>Shell content</p>
    </ProtectedLayout>,
  );
  expect(await screen.findByText("Shell content")).toBeInTheDocument();
  expect(screen.getByText(/owner@example\.com/)).toBeInTheDocument();
  expect(screen.getByText(/Owner/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Account" })).toBeInTheDocument();
});

test("hides the user-management link from non-owners", async () => {
  stubFetchRoutes([
    [
      "/auth/session",
      {
        status: 200,
        body: {
          user: makeUser({ role: "team_member", email: "member@example.com" }),
          csrf_token: "csrf",
        },
      },
    ],
  ]);
  render(
    <ProtectedLayout>
      <p>Shell content</p>
    </ProtectedLayout>,
  );
  expect(await screen.findByText("Shell content")).toBeInTheDocument();
  expect(screen.getByText(/Team member/)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
});

test("logout posts to the API with the CSRF token and redirects to login", async () => {
  const fetchMock = stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser(), csrf_token: "csrf-token" } }],
    ["/auth/logout", { status: 204, body: null }],
  ]);
  render(
    <ProtectedLayout>
      <p>Shell content</p>
    </ProtectedLayout>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Log out" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  const logoutCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/auth/logout"));
  expect(logoutCall).toBeDefined();
  expect((logoutCall![1] as RequestInit).method).toBe("POST");
  expect((logoutCall![1] as RequestInit).headers).toMatchObject({ "X-CSRF-Token": "csrf-token" });
});
