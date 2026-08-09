import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../../layout";
import NewLeadPage from "./page";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push, refresh: vi.fn() }),
}));

afterEach(() => {
  push.mockClear();
  vi.unstubAllGlobals();
});

const FIELDS = [
  {
    id: "dddddddd-0000-0000-0000-000000000001",
    key: "roof_type",
    label: "Roof type",
    type: "select",
    options: ["Shingle", "Metal"],
    required: true,
    is_active: true,
    display_order: 0,
  },
  {
    id: "dddddddd-0000-0000-0000-000000000002",
    key: "sq_footage",
    label: "Square footage",
    type: "number",
    options: null,
    required: false,
    is_active: true,
    display_order: 1,
  },
];

function renderNewLead(role: "owner" | "team_member" = "owner") {
  const fetchMock = stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser({ role }), csrf_token: "csrf" } }],
    ["/custom-fields", { status: 200, body: FIELDS }],
    ["/leads/assignable-users", { status: 200, body: [] }],
    [
      "/leads",
      {
        status: 201,
        body: { id: "eeeeeeee-0000-0000-0000-000000000001" },
      },
    ],
  ]);
  render(
    <ProtectedLayout>
      <NewLeadPage />
    </ProtectedLayout>,
  );
  return fetchMock;
}

test("renders custom fields with required markers and submits their values", async () => {
  const fetchMock = renderNewLead();
  const roofType = await screen.findByLabelText("Roof type *");
  expect(roofType.tagName).toBe("SELECT");
  expect(screen.getByLabelText("Square footage")).toHaveAttribute("type", "number");

  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Beta HVAC" } });
  fireEvent.change(roofType, { target: { value: "Metal" } });
  fireEvent.change(screen.getByLabelText("Square footage"), { target: { value: "2400" } });
  fireEvent.click(screen.getByRole("button", { name: "Create lead" }));

  await waitFor(() => expect(push).toHaveBeenCalledWith("/leads/eeeeeeee-0000-0000-0000-000000000001"));
  const createCall = fetchMock.mock.calls.find(
    ([url, init]) => String(url).endsWith("/api/v1/leads") && (init as RequestInit).method === "POST",
  );
  expect(createCall).toBeDefined();
  const body = JSON.parse(String((createCall![1] as RequestInit).body));
  expect(body.name).toBe("Beta HVAC");
  expect(body.custom_values).toEqual({ roof_type: "Metal", sq_footage: 2400 });
});

test("team members cannot open the create form", async () => {
  renderNewLead("team_member");
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You are not allowed to create leads.",
  );
});
