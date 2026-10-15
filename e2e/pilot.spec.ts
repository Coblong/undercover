import { expect, test } from "@playwright/test";

const decision = (kind: string, payout: number, explanation: string) => ({
  decision: kind,
  payout,
  explanation,
  policy_section: "5. Baggage and Personal Effects",
});

test("the queue lists every claim, all waiting", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /^CLM-/ })).toHaveCount(25);
  await expect(
    page.getByRole("listitem").filter({ hasText: "Waiting" }).first(),
  ).toHaveText("25Waiting");
  await page.getByRole("button", { name: /CLM-1001/ }).click();
  const claim = page.getByRole("article", { name: "Claim CLM-1001" });
  await expect(claim.getByText("$420.00").first()).toBeVisible();
  await expect(claim.getByRole("button", { name: "Run agent" })).toBeVisible();
});

test("a large payout is blocked until a handler signs off", async ({
  page,
  request,
}) => {
  const approved = await request.post("/api/claims/CLM-1002/decision", {
    data: decision(
      "approve",
      1250,
      "The suitcase and its contents are covered.",
    ),
  });
  const outcome = (await approved.json()) as { state: string };
  expect(outcome.state).toBe("waiting_for_sign_off");

  await page.goto("/");
  await page.getByRole("button", { name: /CLM-1002/ }).click();
  const claim = page.getByRole("article", { name: "Claim CLM-1002" });
  await expect(claim.getByText("Waiting for sign-off").first()).toBeVisible();
  await expect(claim.getByRole("button", { name: "Run agent" })).toHaveCount(0);

  const blocked = await request.post("/api/claims/CLM-1002/payment", {
    data: { amount: 1250 },
  });
  expect(blocked.status()).toBe(409);
  await expect(claim.getByText(/^Paid /)).toHaveCount(0);

  await claim.getByLabel("Handler").selectOption({ label: "Dana Whitfield" });
  await claim
    .getByRole("button", { name: "Sign off and pay $1,250.00" })
    .click();

  await expect(claim.getByText("Signed off by Dana Whitfield")).toBeVisible();
  await expect(claim.getByText(/^Paid/)).toContainText("$1,250.00");
  await expect(claim.getByRole("form", { name: "Sign-off" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /CLM-1002/ })).toContainText(
    "Decided",
  );
});

test("a decline shows its reasons and offers nothing to pay", async ({
  page,
  request,
}) => {
  await request.post("/api/claims/CLM-1004/decision", {
    data: decision(
      "decline",
      0,
      "The delay was shorter than the waiting period.",
    ),
  });
  await page.goto("/");
  await page.getByRole("button", { name: /CLM-1004/ }).click();
  const decided = page.getByRole("region", { name: "Decision" });
  await expect(decided).toContainText("Declined");
  await expect(decided).toContainText(
    "The delay was shorter than the waiting period.",
  );
  await expect(decided).toContainText(
    "Policy section: 5. Baggage and Personal Effects",
  );
  await expect(decided).not.toContainText("Paid");
});

test("the steps the agent took are shown in order", async ({
  page,
  request,
}) => {
  const steps = [
    { kind: "tool_call", text: 'get_claim(claim_id="CLM-1003")' },
    { kind: "tool_result", text: '{"claim_id":"CLM-1003"}' },
    { kind: "message", text: "I need the policy next." },
  ];
  for (const data of steps) {
    await request.post("/api/claims/CLM-1003/steps", { data });
  }
  await page.goto("/");
  await page.getByRole("button", { name: /CLM-1003/ }).click();
  await expect(
    page.getByRole("region", { name: "Agent steps" }).getByRole("listitem"),
  ).toHaveText([
    'Calledget_claim(claim_id="CLM-1003")',
    'Got back{"claim_id":"CLM-1003"}',
    "SaidI need the policy next.",
  ]);
});

test("the agent's instructions can be read but not changed", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Instructions" }).click();
  const instructions = page.getByRole("region", { name: "Agent instructions" });
  await expect(instructions).toContainText("You are the UnderCover Agent");
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await expect(instructions.getByRole("button")).toHaveCount(0);
});
