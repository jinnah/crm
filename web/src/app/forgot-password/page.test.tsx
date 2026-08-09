import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { stubFetchRoutes } from "@/test/helpers";
import ForgotPasswordPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

const GENERIC = "If an account exists for that email, a password reset link has been sent.";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders a labeled email field", () => {
  render(<ForgotPasswordPage />);
  expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
});

test("shows the generic confirmation after submitting", async () => {
  stubFetchRoutes([["/auth/forgot-password", { status: 202, body: { detail: GENERIC } }]]);
  render(<ForgotPasswordPage />);
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "anyone@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
  expect(await screen.findByRole("status")).toHaveTextContent(GENERIC);
  // The form is replaced by the confirmation — no account-existence signal.
  expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
});
