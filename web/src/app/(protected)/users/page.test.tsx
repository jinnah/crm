import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../layout";
import UsersPage from "./page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
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

test("owners see the user list, row actions and the creation dialog", async () => {
  stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser(), csrf_token: "csrf" } }],
    [
      "/users",
      {
        status: 200,
        body: {
          items: [
            makeUser(),
            makeUser({
              id: "22222222-2222-2222-2222-222222222222",
              email: "member@example.com",
              role: "team_member",
            }),
          ],
          total: 2,
          page: 1,
          page_size: 25,
        },
      },
    ],
  ]);
  renderUsersPage();

  expect((await screen.findAllByText(/member@example\.com/)).length).toBeGreaterThan(0);
  expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
  expect(screen.getAllByText("Team member").length).toBeGreaterThan(0); // role badge

  // The row shows one styled badge and no unconfirmed inline role control.
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

  // Destructive and sensitive actions sit behind the per-row menu,
  // labelled with the user it acts on.
  const menus = screen.getAllByRole("button", { name: /^Actions for / });
  expect(menus.length).toBe(2);
  fireEvent.click(screen.getByRole("button", { name: "Actions for member@example.com" }));
  expect(screen.getByRole("menuitem", { name: "Set temporary password" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Deactivate" })).toBeInTheDocument();

  // Deactivation asks for confirmation before anything happens.
  fireEvent.click(screen.getByRole("menuitem", { name: "Deactivate" }));
  expect(
    await screen.findByRole("dialog", { name: /Deactivate member@example\.com/ }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  // Creation opens in its own dialog.
  fireEvent.click(screen.getByRole("button", { name: "Create user" }));
  const dialog = await screen.findByRole("dialog", { name: "Create user" });
  expect(dialog).toBeInTheDocument();
  expect(screen.getByLabelText("Temporary password")).toBeInTheDocument();
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
  expect(screen.queryByRole("button", { name: "Create user" })).not.toBeInTheDocument();
});
