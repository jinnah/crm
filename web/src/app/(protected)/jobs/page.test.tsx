import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Job } from "@/lib/api";
import { makeUser, stubFetchRoutes } from "@/test/helpers";
import ProtectedLayout from "../layout";
import JobsPage from "./page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/jobs",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "aaaaaaaa-0000-0000-0000-000000000001",
    job_number: "J-2026-0001",
    lead_id: "bbbbbbbb-0000-0000-0000-000000000001",
    lead_name: "Pat Customer",
    title: "Roof replacement",
    service_type: "Roofing",
    service_address: "12 Elm Street",
    status: "quoted",
    assigned_to: null,
    assignee_name: null,
    scheduled_for: null,
    started_at: null,
    completed_at: null,
    internal_notes: "",
    archived_at: null,
    created_at: "2026-08-09T12:00:00Z",
    updated_at: "2026-08-09T12:00:00Z",
    ...overrides,
  };
}

function renderJobs(jobs: Job[]) {
  stubFetchRoutes([
    ["/auth/session", { status: 200, body: { user: makeUser(), csrf_token: "csrf" } }],
    ["/leads/assignable-users", { status: 200, body: [] }],
    [
      "/jobs?",
      { status: 200, body: { items: jobs, total: jobs.length, page: 1, page_size: 25 } },
    ],
  ]);
  return render(
    <ProtectedLayout>
      <JobsPage />
    </ProtectedLayout>,
  );
}

test("lists jobs with customer and appointment context", async () => {
  renderJobs([makeJob(), makeJob({ id: "a2", job_number: "J-2026-0002", status: "completed" })]);
  expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  const links = await screen.findAllByRole("link", { name: "J-2026-0001" });
  expect(links[0]).toHaveAttribute("href", "/jobs/aaaaaaaa-0000-0000-0000-000000000001");
  expect(screen.getAllByText("Pat Customer").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Quoted").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
  // Search + filters exist for job number, status and archived state.
  expect(screen.getByLabelText("Search")).toBeInTheDocument();
  expect(screen.getByLabelText("Status")).toBeInTheDocument();
  expect(screen.getByLabelText("Archived")).toBeInTheDocument();
});

test("empty state explains where jobs come from", async () => {
  renderJobs([]);
  expect(
    await screen.findByRole("heading", { name: "No jobs match these filters" }),
  ).toBeInTheDocument();
});
