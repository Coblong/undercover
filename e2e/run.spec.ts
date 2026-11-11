import { expect, MARCUS, signInAs, test, tokenFor } from "./fixtures";
import { execute } from "./database";

// The agent is never run here. A run and its work are set up directly and
// through the API, and the tests check what the dashboard shows.

test("a claim left for a handler is labeled and cannot be run", async ({
  page,
}) => {
  execute(
    "UPDATE claims SET state = 'left_for_handler' WHERE claim_id = ?",
    "CLM-1006",
  );
  await page.goto("/");
  const row = page.getByRole("button", { name: /CLM-1006/ });
  await expect(row).toContainText("Left for a handler");
  await expect(
    page
      .getByRole("region", { name: "Queue" })
      .getByRole("listitem")
      .filter({ hasText: "Left for a handler" })
      .first(),
  ).toHaveText("1Left for a handler");
  await row.click();
  const claim = page.getByRole("article", { name: "Claim CLM-1006" });
  await expect(claim).toContainText("A handler needs to look at it");
  await expect(claim.getByRole("button", { name: "Run agent" })).toHaveCount(0);
});

test("what the agent read is listed on the claim", async ({
  page,
  request,
}) => {
  const reads = [
    "Policy wording, 3. Trip Delay",
    "Claims manual, 10. Meals without receipts",
  ];
  for (const text of reads) {
    await request.post("/api/claims/CLM-1007/steps", {
      data: { kind: "read", text },
    });
  }
  await page.goto("/");
  await page.getByRole("button", { name: /CLM-1007/ }).click();
  await expect(
    page.getByRole("region", { name: "What it read" }).getByRole("listitem"),
  ).toHaveText(reads);
});

test("a handler who can't sign off is told so, and the backend refuses it too", async ({
  page,
  request,
}) => {
  await request.post("/api/claims/CLM-1015/decision", {
    data: {
      decision: "approve",
      payout: 1500,
      explanation: "Covered.",
      policy_section: "3. Trip Delay",
    },
  });
  await signInAs(page, MARCUS);
  await page.getByRole("button", { name: /CLM-1015/ }).click();
  const claim = page.getByRole("article", { name: "Claim CLM-1015" });
  await expect(
    claim.getByText(/Marcus Oyelaran cannot sign off payouts over the limit/),
  ).toBeVisible();
  await expect(claim.getByRole("button", { name: /Sign off/ })).toHaveCount(0);

  const token = await tokenFor(request, MARCUS);
  const refused = await request.post("/api/claims/CLM-1015/sign-off", {
    headers: { authorization: `Bearer ${token}` },
  });
  expect(refused.status()).toBe(403);
  await expect(claim.getByText("Waiting for sign-off").first()).toBeVisible();
  const calls = (await (
    await request.get("http://127.0.0.1:8003/received")
  ).json()) as { claim_id: string }[];
  expect(calls.filter((call) => call.claim_id === "CLM-1015")).toHaveLength(0);
});
