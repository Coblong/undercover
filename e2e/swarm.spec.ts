import { type APIRequestContext, expect, test } from "@playwright/test";

// The agent is never run here. The stand-in for the agent's service records
// what the backend asks it, and the tests play a dispatch out through the API.

interface Started {
  kind: string;
  concurrency: number;
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
