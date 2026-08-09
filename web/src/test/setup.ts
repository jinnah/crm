import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest globals are disabled, so Testing Library's automatic cleanup does
// not register itself; run it explicitly between tests.
afterEach(() => {
  cleanup();
});
