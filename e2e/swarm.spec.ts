import { type APIRequestContext } from "@playwright/test";
import { expect, test } from "./fixtures";

// The agent is never run here. The stand-in for the agent's service records
// what the backend asks it, and the tests play a dispatch out through the API.

interface Started {
  kind: string;
  concurrency: number;
  eval_set?: string;
  tiny?: boolean;
}

async function started(request: APIRequestContext): Promise<Started[]> {
  const response = await request.get("http://127.0.0.1:8003/started");
  return (await response.json()) as Started[];
}

async function endRun(request: APIRequestContext, state = "finished") {
  const latest = await request.get("/api/dispatches/latest");
  const { run_id: runId } = (await latest.json()) as { run_id: number };
  await request.post(`/api/runs/${String(runId)}/end`, { data: { state } });
}

async function endLatestEvaluation(request: APIRequestContext) {
  const listed = await request.get("/api/evaluations");
  const [latest] = (await listed.json()) as { run_id: number }[];
  await request.post(`/api/runs/${String(latest.run_id)}/end`, {
    data: { state: "finished" },
  });
}

test("starting a dispatch reaches the agent's service with the number chosen", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const dispatch = page.getByRole("region", { name: "Dispatch" });
  const agents = dispatch.getByLabel("Claim agents at once");
  await expect(agents).toHaveValue("3");
  await agents.fill("7");
  await dispatch.getByRole("button", { name: "Start a dispatch" }).click();
  await expect(dispatch).toContainText("Running");
  await expect(dispatch).toContainText("up to 7 claim agents at once");
  await expect(
    dispatch.getByRole("button", { name: "Start a dispatch" }),
  ).toBeDisabled();
  expect(await started(request)).toEqual([
    { kind: "dispatch", concurrency: 7 },
  ]);
  await endRun(request);
});

test("a second start is refused with the reason while one runs", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const dispatch = page.getByRole("region", { name: "Dispatch" });
  await expect(
    dispatch.getByRole("button", { name: "Start a dispatch" }),
  ).toBeEnabled();
  const other = await request.post("/api/dispatches", {
    data: { concurrency: 2 },
  });
  expect(other.status()).toBe(202);
  await dispatch.getByRole("button", { name: "Start a dispatch" }).click();
  await expect(dispatch.getByRole("alert")).toHaveText(
    "Could not start the dispatch: A dispatch or an evaluation is already running",
  );
  await endRun(request);
});

test("a dispatch played out through the API shows its claims, cost and summary", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/dispatches", {
    data: { concurrency: 3 },
  });
  const { run_id: runId } = (await created.json()) as { run_id: number };
  const run = `/api/runs/${String(runId)}`;
  await request.post(`${run}/plan`);
  const decision = (kind: string, payout: number) => ({
    decision: kind,
    payout,
    explanation: "Covered.",
    policy_section: "3. Trip Delay",
  });
  for (const claim of ["CLM-1021", "CLM-1022", "CLM-1010", "CLM-1024"]) {
    await request.post(`${run}/claims/${claim}/start`);
    await request.post(`/api/claims/${claim}/model-calls`, {
      data: { run_id: runId, input_tokens: 900, output_tokens: 40, cost: 0.01 },
    });
    await request.post(`/api/claims/${claim}/work-times`, {
      data: { run_id: runId, seconds: 10 },
    });
  }
  await request.post("/api/claims/CLM-1021/decision", {
    data: decision("approve", 420),
  });
  await request.post("/api/claims/CLM-1021/payment", { data: { amount: 420 } });
  await request.post("/api/claims/CLM-1022/decision", {
    data: decision("decline", 0),
  });
  await request.post("/api/claims/CLM-1010/decision", {
    data: decision("approve", 1250),
  });
  for (const claim of ["CLM-1021", "CLM-1022", "CLM-1010"]) {
    await request.post(`/api/claims/${claim}/attempt/end`, {
      data: { error: null },
    });
  }
  await endRun(request);

  await page.goto("/");
  const dispatch = page.getByRole("region", { name: "Dispatch" });
  await expect(dispatch).toContainText("Finished");
  await expect(dispatch.getByLabel("Summary")).toHaveText(
    "2 approved, 1 declined, 0 referred." +
      "Waiting for a sign-off: CLM-1010 $1,250.00." +
      "Still in progress: CLM-1024.",
  );
  await expect(dispatch).toContainText("$0.0400");
  const row = dispatch.getByRole("row", { name: /CLM-1021/ });
  await expect(row).toContainText("Decided");
  await expect(row).toContainText("Approved $420.00");
  await expect(row).toContainText("10.0 s");
  await expect(row).toContainText("$0.0100");
  await expect(dispatch.getByRole("row", { name: /CLM-1024/ })).toContainText(
    "In progress",
  );
});

test("starting an evaluation reaches the agent's service with the set and the number", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Scoreboard" }).click();
  const board = page.getByRole("region", { name: "Scoreboard" });
  await board.getByLabel("Set").selectOption("held_out");
  await board.getByLabel("Just the 5 tiny claims").check();
  await board.getByLabel("Claim agents at once").fill("5");
  await board.getByRole("button", { name: "Start an evaluation" }).click();
  const table = board.getByRole("table", { name: "Evaluations" });
  await expect(table.getByRole("row").nth(1)).toContainText("Running");
  await expect(table.getByRole("row").nth(1)).toContainText("Held-out set5");
  const calls = await started(request);
  expect(calls.at(-1)).toEqual({
    kind: "evaluation",
    concurrency: 5,
    eval_set: "held_out",
    tiny: true,
  });
  await expect(
    board.getByRole("button", { name: "Start an evaluation" }),
  ).toBeDisabled();
  await endLatestEvaluation(request);
});

test("an evaluation played out through the API is scored on the scoreboard and leaves the queue as it was", async ({
  page,
  request,
}) => {
  const counts = () => page.locator("ul.counts li").allTextContents();
  await page.goto("/");
  await expect(page.locator("ul.counts li")).toHaveCount(5);
  const before = await counts();

  const created = await request.post("/api/runs/evaluation", {
    data: {
      concurrency: 5,
      eval_set: "held_out",
      tiny: true,
    },
  });
  const { run_id: runId } = (await created.json()) as { run_id: number };
  const answers: [string, string, number][] = [
    ["CLM-1104", "approve", 250],
    ["CLM-1106", "approve", 250],
    ["CLM-1110", "refer", 0],
    ["CLM-1112", "approve", 680],
    ["CLM-1114", "approve", 1250],
  ];
  for (const [source, kind, payout] of answers) {
    const copy = `EV${String(runId)}-${source}`;
    await request.post(`/api/runs/${String(runId)}/claims/${copy}/start`);
    await request.post(`/api/claims/${copy}/decision`, {
      data: {
        decision: kind,
        payout,
        explanation: "Stub.",
        policy_section: "3. Trip Delay",
      },
    });
    await request.post(`/api/claims/${copy}/model-calls`, {
      data: { run_id: runId, input_tokens: 9, output_tokens: 1, cost: 0.01 },
    });
    await request.post(`/api/claims/${copy}/work-times`, {
      data: { run_id: runId, seconds: 10 },
    });
    await request.post(`/api/claims/${copy}/attempt/end`, {
      data: { error: null },
    });
  }
  await request.post(`/api/runs/${String(runId)}/end`, {
    data: { state: "finished" },
  });

  await page.reload();
  await expect(page.locator("ul.counts li")).toHaveCount(5);
  expect(await counts()).toEqual(before);
  await page.getByRole("button", { name: "Scoreboard" }).click();
  const newest = page
    .getByRole("table", { name: "Evaluations" })
    .getByRole("row")
    .nth(1);
  await expect(newest).toContainText("Finished");
  // Four of five decisions match; one wrongful approval of $1,250.00.
  await expect(newest.getByRole("cell").nth(8)).toHaveText("80%");
  await expect(newest.getByRole("cell").nth(9)).toHaveText("80%");
  await expect(newest.getByRole("cell").nth(10)).toHaveText("$1,250.00");
  await expect(newest.getByRole("cell").nth(11)).toHaveText("80%");
  // The judge stand-in grades every explanation 2.4 and charges $0.0002 for it.
  await expect(newest.getByRole("cell").nth(12)).toHaveText("2.40");
  await expect(newest.getByRole("cell").nth(13)).toHaveText("$0.0100");
  await expect(newest.getByRole("cell").nth(14)).toHaveText("$0.0010");
  await expect(newest.getByRole("cell").nth(15)).toHaveText("10.0 s");

  await newest.getByRole("button", { name: String(runId) }).click();
  const claims = page.getByRole("table", { name: "Evaluation claims" });
  const miss = claims.getByRole("row", { name: /CLM-1114/ });
  await expect(miss).toContainText("Declined");
  await expect(miss).toContainText("Approved $1,250.00");
  await expect(miss).toContainText("Miss");
  await expect(claims.getByRole("row", { name: /CLM-1104/ })).toContainText(
    "Match",
  );
  await expect(claims.getByRole("row", { name: /CLM-1104/ })).toContainText(
    "B 2.40YesNo",
  );
  await expect(claims.getByText("Miss")).toHaveCount(1);

  await miss.getByRole("button", { name: /^EV/ }).click();
  const copy = page.getByRole("article", { name: /^Claim EV/ });
  await expect(copy).toContainText("Evaluation copy");
  await expect(copy).toContainText("counts as finished");
  await expect(copy.getByRole("button", { name: /Sign off/ })).toHaveCount(0);
  await expect(copy.getByRole("button", { name: "Run agent" })).toHaveCount(0);
  expect(await counts()).toEqual(before);
});
