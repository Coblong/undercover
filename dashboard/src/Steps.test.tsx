import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { stubApi } from "./fixtures";
import { Steps } from "./Steps";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const steps = [
  {
    id: 1,
    kind: "tool_call",
    text: 'get_claim(claim_id="CLM-1001")',
    created_at: "x",
  },
  { id: 2, kind: "tool_result", text: "{}", created_at: "x" },
  { id: 3, kind: "message", text: "Approved, $420.00.", created_at: "x" },
  {
    id: 5,
    kind: "read",
    text: "Claims manual, 9. Repeat claims",
    created_at: "x",
  },
  { id: 4, kind: "error", text: "The agent stopped.", created_at: "x" },
];

it("lists the steps in order with what each one is", async () => {
  stubApi({ "/api/claims/CLM-1001/steps": steps });
  render(<Steps claimId="CLM-1001" active={false} />);
  const list = await screen.findByRole("region", { name: "Agent steps" });
  expect(
    within(list)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual([
    'Calledget_claim(claim_id="CLM-1001")',
    "Got back{}",
    "SaidApproved, $420.00.",
    "ReadClaims manual, 9. Repeat claims",
    "ProblemThe agent stopped.",
  ]);
});

it("lists what the agent read above its steps", async () => {
  stubApi({ "/api/claims/CLM-1001/steps": steps });
  render(<Steps claimId="CLM-1001" active={false} />);
  const read = await screen.findByRole("region", { name: "What it read" });
  expect(
    within(read)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual(["Claims manual, 9. Repeat claims"]);
});

it("has no list of reads when the agent read nothing", async () => {
  stubApi({ "/api/claims/CLM-1001/steps": steps.slice(0, 2) });
  render(<Steps claimId="CLM-1001" active={false} />);
  await screen.findByRole("region", { name: "Agent steps" });
  expect(screen.queryByRole("region", { name: "What it read" })).toBeNull();
});

it("says the agent is working while a claim is in progress", async () => {
  stubApi({ "/api/claims/CLM-1001/steps": [] });
  render(<Steps claimId="CLM-1001" active />);
  expect(await screen.findByText("Working on this claim now.")).toBeDefined();
});

it("shows nothing for a claim the agent has not touched", async () => {
  const fetchStub = stubApi({ "/api/claims/CLM-1001/steps": [] });
  const { container } = render(<Steps claimId="CLM-1001" active={false} />);
  await vi.waitFor(() => {
    expect(fetchStub).toHaveBeenCalled();
  });
  expect(container.textContent).toBe("");
});
