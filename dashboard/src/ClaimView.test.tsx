import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ClaimView } from "./ClaimView";
import { claim, claimRoutes, decidedClaim, stubApi } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the claim in full", async () => {
  stubApi({ "/api/claims/CLM-1001": claim, ...claimRoutes });
  render(<ClaimView claimId="CLM-1001" />);
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
  render(<ClaimView claimId="CLM-1001" />);
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
  render(<ClaimView claimId="CLM-1001" />);
  expect(await screen.findByText(/Signed off by Dana Whitfield/)).toBeDefined();
});

it("says so when there are no earlier claims", async () => {
  stubApi({
    "/api/claims/CLM-1001": claim,
    "/api/policies/POL-2001": claimRoutes["/api/policies/POL-2001"],
    "/api/customers/CUS-3001/claim-history": {
      customer_id: "CUS-3001",
      previous_claims: [],
    },
  });
  render(<ClaimView claimId="CLM-1001" />);
  expect(await screen.findByText("No earlier claims.")).toBeDefined();
});

it("shows a loading message, then an error when the claim cannot be loaded", async () => {
  stubApi({});
  render(<ClaimView claimId="CLM-1001" />);
  expect(screen.getByRole("status").textContent).toBe("Loading claim...");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load this claim.",
  );
});
