import { type APIRequestContext } from "@playwright/test";
import { expect, MARCUS, signInAs, test } from "./fixtures";

// The improver is never run here. The stand-in for the agent's service records what
// the backend asks it and records the handler's decision as the real service would.
// The tests play out the rest of the service's work through the API.

const BASE = "/api/improvement/rounds";

/** Stop any round a test before this one left open, and return a way to name the next. */
async function clear(request: APIRequestContext): Promise<void> {
  const latest = await request.get("/api/rounds/latest");
  const round = (await latest.json()) as { id: number; state: string } | null;
  if (round?.state === "open")
    await request.post(`/api/rounds/${String(round.id)}/stop`);
}

async function open(request: APIRequestContext): Promise<string> {
  const created = await request.post(BASE, { data: { handler_id: "HND-01" } });
  const round = (await created.json()) as { id: number };
  return `${BASE}/${String(round.id)}`;
}

interface Received {
  started: { handler_id: string }[];
  decided: {
    round_id: number;
    outcome: string;
    handler_id: string;
    reason: string | null;
  }[];
}

async function received(request: APIRequestContext): Promise<Received> {
  const response = await request.get("http://127.0.0.1:8003/rounds-received");
  return (await response.json()) as Received;
}

async function stage(
  request: APIRequestContext,
  round: string,
  name: string,
  done: number,
  of: number,
) {
  await request.post(`${round}/stage`, { data: { stage: name, done, of } });
}

async function latest(request: APIRequestContext): Promise<{ id: number }> {
  return (await (await request.get("/api/rounds/latest")).json()) as {
    id: number;
  };
}

const change = {
  kind: "change",
  instructions: "Decide the claim.\nNever approve what the wording declines.",
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  recipe: null,
  reason: "Approvals of declined claims need a rule.",
};

test("a round is started, followed, approved and rejected from the Improvement page", async ({
  page,
  request,
}) => {
  await clear(request);
  await page.getByRole("button", { name: "Improvement" }).click();
  await expect(
    page.getByRole("heading", { name: "Improvement", level: 2 }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Start a round" }).click();
  const start = page.getByRole("dialog", { name: "Start a round?" });
  await expect(start).toContainText("up to $0.50");
  await start.getByRole("button", { name: "Start the round" }).click();
  const current = page.getByRole("region", { name: "Current round" });
  await expect(current).toContainText("Open");
  const id = (await latest(request)).id;
  const round = `${BASE}/${String(id)}`;
  await expect(current).toContainText(`Round ${String(id)}`);
  expect((await received(request)).started.at(-1)).toEqual(
    expect.objectContaining({ handler_id: "HND-01" }),
  );

  // The service reports each step, and the page shows it as it happens.
  await stage(request, round, "first_evaluations", 2, 3);
  const steps = page.getByRole("list", { name: "Where the round has got to" });
  await expect(steps).toContainText("First evaluations (2 of 3)");
  await stage(request, round, "improver_working", 0, 0);
  await expect(steps.getByRole("listitem").nth(1)).toHaveAttribute(
    "aria-current",
    "step",
  );

  await request.post(`${round}/proposal`, { data: change });
  const proposal = page.getByRole("region", { name: "Waiting for approval" });
  await expect(proposal).toContainText(
    "A change is waiting for approval: version 2",
  );
  await expect(proposal.getByRole("group", { name: "Changes" })).toContainText(
    "Never approve what the wording declines.",
  );
  await proposal.getByRole("button", { name: "Approve" }).click();
  await page
    .getByRole("dialog", { name: "Approve version 2?" })
    .getByRole("button", { name: "Approve" })
    .click();
  await expect(proposal).toHaveCount(0);
  expect((await received(request)).decided.at(-1)).toEqual(
    expect.objectContaining({
      round_id: id,
      outcome: "approved",
      handler_id: "HND-01",
    }),
  );

  // The service evaluates the version and the backend rolls it back.
  await stage(request, round, "evaluating", 3, 3);
  await expect(steps).toContainText("Evaluations (3 of 3)");
  await request.post(`${round}/verdict`, {
    data: { reason: "the round could not afford to evaluate it" },
  });
  const recipe = {
    kind: "recipe",
    instructions: null,
    model: null,
    reasoning_effort: null,
    recipe: "1. Read.\n2. Propose.",
    reason: "A shorter recipe.",
  };
  await request.post(`${round}/proposal`, { data: recipe });
  await expect(proposal).toContainText("A new recipe is waiting for approval");
  await proposal.getByRole("button", { name: "Reject" }).click();
  const reject = page.getByRole("dialog", { name: "Reject recipe version 2?" });
  await expect(reject.getByRole("button", { name: "Reject" })).toBeDisabled();
  await reject.getByRole("textbox").fill("Too short.");
  await reject.getByRole("button", { name: "Reject" }).click();
  await expect(proposal).toHaveCount(0);
  expect((await received(request)).decided.at(-1)).toEqual(
    expect.objectContaining({ outcome: "rejected", reason: "Too short." }),
  );

  await request.post(`${round}/end`, {
    data: { state: "finished", why: null },
  });
  const rounds = page.getByRole("table", { name: "Rounds" });
  await expect(rounds).toContainText("Finished");
  await expect(rounds).toContainText("Rolled back");
  await expect(rounds).toContainText(
    "the round could not afford to evaluate it",
  );
  await expect(rounds).toContainText("Approved the change - Dana Whitfield");
  await expect(rounds).toContainText("Rejected the recipe - Dana Whitfield");
  await expect(rounds).toContainText('("Too short.")');

  // The version it tried is on the Configuration page, and was not kept.
  await page.getByRole("button", { name: "Configuration" }).click();
  const versions = page.getByRole("region", { name: "Versions" });
  await expect(
    versions.getByRole("row", { name: /Improver, round 1/ }),
  ).toContainText("Dana Whitfield");
  await expect(
    page.getByRole("region", { name: "Current version" }),
  ).toContainText("Version 1");
});

test("a handler who can't sign off over the limit sees the round but can't act on it", async ({
  page,
  request,
}) => {
  await clear(request);
  const round = await open(request);
  await request.post(`${round}/proposal`, { data: change });
  await signInAs(page, MARCUS);
  await page.getByRole("button", { name: "Improvement" }).click();
  await expect(page.getByRole("note")).toContainText(
    "Marcus Oyelaran can see the rounds but not start, stop or decide on one.",
  );
  await expect(
    page.getByRole("region", { name: "Waiting for approval" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start a round" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "Stop the round" }),
  ).toHaveCount(0);
});

test("a handler stops an open round after a confirmation", async ({
  page,
  request,
}) => {
  await clear(request);
  await open(request);
  await page.getByRole("button", { name: "Improvement" }).click();
  await page.getByRole("button", { name: "Stop the round" }).click();
  await page
    .getByRole("dialog", { name: "Stop this round?" })
    .getByRole("button", { name: "Stop the round" })
    .click();
  await expect(page.getByText(/Stopped by Dana Whitfield/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start a round" }),
  ).toBeVisible();
});
