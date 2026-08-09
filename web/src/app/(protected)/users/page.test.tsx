import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../layout";
import UsersPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderUsersPage() {
  return render(
    <ProtectedLayout>
      <UsersPage />
    </ProtectedLayout>,
  );
}

test("owners see the user list and creation form", async () => {
  stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser(), csrf_token: "csrf" } }],
    [
      "/users",
      {
        status: 200,
        body: [
          makeUser(),
          makeUser({
            id: "22222222-2222-2222-2222-222222222222",
            email: "member@example.com",
            role: "team_member",
          }),
        ],
      },
    ],
  ]);
  renderUsersPage();

  expect((await screen.findAllByText(/member@example\.com/)).length).toBeGreaterThan(0);
  expect(screen.getByRole("heading", { name: "User management" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Create user" })).toBeInTheDocument();
  expect(screen.getByLabelText("Temporary password")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Deactivate" }).length).toBeGreaterThan(0);
  expect(screen.getAllByRole("button", { name: "Set temporary password" }).length).toBeGreaterThan(
    0,
  );
});

test("non-owners get an access message and no user list is fetched", async () => {
  const fetchMock = stubFetchRoutes([
    [
      "/auth/session",
      {
        status: 200,
        body: { user: makeUser({ role: "manager", email: "manager@example.com" }), csrf_token: "csrf" },
      },
    ],
  ]);
  renderUsersPage();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You do not have access to user management.",
  );
  await waitFor(() => {
    const userListCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("/api/v1/users"),
    );
    expect(userListCalls).toHaveLength(0);
  });
  expect(screen.queryByRole("heading", { name: "Create user" })).not.toBeInTheDocument();
});
