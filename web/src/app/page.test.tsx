import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Home from "./page";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: "ok", service: "crm-api" }),
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders the project name and API status", async () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "Service CRM" })).toBeInTheDocument();
  expect(await screen.findByText("Connected")).toBeInTheDocument();
});
