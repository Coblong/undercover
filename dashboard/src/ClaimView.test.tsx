import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ClaimView } from "./ClaimView";
import {
  claim,
  claimRoutes,
  decidedClaim,
  handlers,
  stubApi,
  waitingForSignOff,
} from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the claim in full", async () => {
  stubApi({ "/api/claims/CLM-1001": claim, ...claimRoutes });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(
    await screen.findByText("My flight home from Rome was canceled."),
  ).toBeDefined();
  expect(screen.getByText("Airport hotel")).toBeDefined();
  expect(
    screen.getByRole("row", { name: /Total claimed/ }).textContent,
  ).toContain("$420.00");
  expect(screen.getByText(/Hotel receipt/)).toBeDefined();
  expect(screen.queryByRole("region", { name: "Decision" })).toBeNull();
  expect(await screen.findByText("Premium")).toBeDefined();
  expect(
    await screen.findByText(/Checked bag arrived 30 hours late/),
  ).toBeDefined();
});

it("shows the decision, its reasons and the payment", async () => {
  stubApi({ "/api/claims/CLM-1001": decidedClaim, ...claimRoutes });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  const decision = await screen.findByRole("region", { name: "Decision" });
  expect(decision.textContent).toContain("Approved");
  expect(decision.textContent).toContain("The delay passed the waiting period");
  expect(decision.textContent).toContain("Policy section: 3. Trip Delay");
  expect(decision.textContent).toContain("Paid $420.00");
});

it("shows who signed off", async () => {
  const signedOff = {
    ...decidedClaim,
    sign_off: {
      handler_id: "HND-01",
      handler_name: "Dana Whitfield",
      signed_off_at: "2026-10-15T10:00:00+00:00",
    },
  };
  stubApi({ "/api/claims/CLM-1001": signedOff, ...claimRoutes });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(await screen.findByText(/Signed off by Dana Whitfield/)).toBeDefined();
});

it("says so when there are no earlier claims", async () => {
  stubApi({
    "/api/claims/CLM-1001": claim,
    "/api/policies/POL-2001": claimRoutes["/api/policies/POL-2001"],
    "/api/claims/CLM-1001/claim-history": {
      customer_id: "CUS-3001",
      previous_claims: [],
    },
  });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(await screen.findByText("No earlier claims.")).toBeDefined();
});

it("shows a loading message, then an error when the claim cannot be loaded", async () => {
  stubApi({});
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toBe("Loading claim...");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load this claim.",
  );
});

it("offers to run the agent on a waiting claim, and reloads when it starts", async () => {
  const fetchStub = stubApi({
    "/api/claims/CLM-1001": claim,
    "POST /api/claims/CLM-1001/run": {},
    ...claimRoutes,
  });
  const onChange = vi.fn();
  render(<ClaimView claimId="CLM-1001" onChange={onChange} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Run agent" }),
  );
  await vi.waitFor(() => {
    expect(onChange).toHaveBeenCalled();
  });
  const claimLoads = fetchStub.mock.calls.filter(
    ([path]) => path === "/api/claims/CLM-1001",
  );
  expect(claimLoads).toHaveLength(2);
});

it("offers no run button once the claim is decided", async () => {
  stubApi({ "/api/claims/CLM-1001": decidedClaim, ...claimRoutes });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  await screen.findByRole("region", { name: "Decision" });
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("asks for a sign-off on a large payout", async () => {
  stubApi({
    "/api/claims/CLM-1001": waitingForSignOff,
    "/api/handlers": handlers,
    ...claimRoutes,
  });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(await screen.findByRole("form", { name: "Sign-off" })).toBeDefined();
  expect(screen.getByText("Approved")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("follows the agent while it works, then stops", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let current: unknown = { ...claim, state: "in_progress" };
  let steps = [
    { id: 1, kind: "tool_call", text: "get_claim()", created_at: "x" },
  ];
  const fetchStub = stubApi({
    "/api/claims/CLM-1001": () => current,
    "/api/claims/CLM-1001/steps": () => steps,
    ...claimRoutes,
  });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(await screen.findByText("get_claim()")).toBeDefined();
  expect(screen.getByText("Working on this claim now.")).toBeDefined();

  current = decidedClaim;
  steps = [
    ...steps,
    { id: 2, kind: "message", text: "Approved, $420.00.", created_at: "x" },
  ];
  await act(() => vi.advanceTimersByTimeAsync(2000));
  expect(await screen.findByText("Approved, $420.00.")).toBeDefined();
  expect(screen.queryByText("Working on this claim now.")).toBeNull();

  const loads = fetchStub.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(fetchStub.mock.calls).toHaveLength(loads);
  vi.useRealTimers();
});

it("says a claim left for a handler needs one, and offers no run", async () => {
  const left = { ...claim, state: "left_for_handler" as const };
  stubApi({ "/api/claims/CLM-1001": left, ...claimRoutes });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(
    await screen.findByText(/A handler needs to look at it/),
  ).toBeDefined();
  expect(screen.getByText("Left for a handler")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Run agent" })).toBeNull();
});

it("shows items and documents that share a name without a key warning", async () => {
  const warn = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const twice = {
    ...claim,
    items: [claim.items[0], claim.items[0]],
    documents: [claim.documents[0], claim.documents[0]],
  };
  stubApi({ "/api/claims/CLM-1001": twice, ...claimRoutes });
  render(<ClaimView claimId="CLM-1001" onChange={vi.fn()} />);
  expect(await screen.findAllByText("Airport hotel")).toHaveLength(2);
  expect(screen.getAllByText(/Hotel receipt/)).toHaveLength(2);
  expect(warn).not.toHaveBeenCalled();
  warn.mockRestore();
});
