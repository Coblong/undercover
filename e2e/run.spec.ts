import { expect, test } from "@playwright/test";
import { execute } from "./database";

// The agent is never run here. A run and its work are set up directly and
// through the API, and the tests check what the dashboard shows.

test("the to-do list of a run is shown and ticked off without a reload", async ({
  page,
  request,
}) => {
  execute(
    "INSERT INTO runs (claim_limit, state, summary, started_at) VALUES (?, 'running', '', ?)",
    5,
    "2026-10-26T10:00:00+00:00",
  );
  const list = (first: string, second: string) => ({
    todos: [
      { text: "CLM-1010 delay", status: first },
      { text: "CLM-1011 baggage", status: second },
      { text: "CLM-1012 medical", status: "to_do" },
    ],
  });
  const runs = await request.get("/api/runs/latest");
  const { run_id: runId } = (await runs.json()) as { run_id: number };
  await request.put(`/api/runs/${String(runId)}/todos`, {
    data: list("in_progress", "to_do"),
  });

  await page.goto("/");
  const todos = page.getByRole("list", { name: "To-do list" });
  await expect(todos.getByRole("listitem")).toHaveText([
    "In progressCLM-1010 delay",
    "To doCLM-1011 baggage",
    "To doCLM-1012 medical",
  ]);
  await expect(page.getByRole("region", { name: "Run" })).toContainText(
    "Running",
  );

  await request.put(`/api/runs/${String(runId)}/todos`, {
    data: list("done", "left_for_handler"),
  });
  await expect(todos.getByRole("listitem")).toHaveText([
    "DoneCLM-1010 delay",
    "Left for a handlerCLM-1011 baggage",
    "To doCLM-1012 medical",
  ]);
  await expect(
    page.getByRole("button", { name: "Run the queue" }),
  ).toBeDisabled();
});

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

test("both sets of instructions are shown and cannot be changed", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Instructions" }).click();
  const instructions = page.getByRole("region", { name: "Agent instructions" });
  await expect(
    instructions.getByRole("heading", { name: "Running the queue" }),
  ).toBeVisible();
  await expect(
    instructions.getByRole("heading", { name: "Working a claim" }),
  ).toBeVisible();
  await expect(instructions).toContainText("write_todos");
  await expect(instructions).toContainText("get_document_section");
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
