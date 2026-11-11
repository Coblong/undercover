import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  dana,
  finishedRound,
  marcus,
  Refusal,
  recipes,
  settingsView,
  stubApi,
  waitingRound,
} from "./fixtures";
import { Improvement } from "./Improvement";
import { MeContext } from "./Me";
import type { Handler, Round } from "./types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function page(me: Handler = dana) {
  return render(
    <MeContext value={me}>
      <Improvement />
    </MeContext>,
  );
}

const common = { "/api/recipes": recipes, "/api/settings": settingsView };

it("says what the page is for and invites a first round when there are none", async () => {
  stubApi({ ...common, "/api/rounds": [] });
  page();
  expect(await screen.findByText(/No rounds yet. Start a round/)).toBeDefined();
  expect(
    screen.getByRole("heading", { name: "Improvement", level: 2 }),
  ).toBeDefined();
  expect(screen.getByText(/The improver proposes changes/)).toBeDefined();
  expect(screen.getByRole("button", { name: "Start a round" })).toBeDefined();
  expect(
    screen.getByText(
      "No version has been tried yet, so there is nothing to chart.",
    ),
  ).toBeDefined();
});

it("shows every step, with the one the round is at and its count", async () => {
  stubApi({
    ...common,
    "/api/rounds": [
      {
        ...waitingRound,
        stage: "evaluating",
        done: 2,
        of: 3,
        pending: null,
      },
    ],
  });
  page();
  const steps = await screen.findByRole("list", {
    name: "Where the round has got to",
  });
  const items = within(steps).getAllByRole("listitem");
  expect(items).toHaveLength(7);
  expect(items.map((i) => i.textContent)).toEqual([
    "First evaluationsDone",
    "The improver workingDone",
    "A change waiting for approvalDone",
    "Evaluations (2 of 3)Now",
    "VerdictNot yet",
    "A recipe waiting for approvalNot yet",
    "FinishedNot yet",
  ]);
  expect(items[3].getAttribute("aria-current")).toBe("step");
});

it("shows a proposal waiting with its diff and the model before and after", async () => {
  stubApi({ ...common, "/api/rounds": [waitingRound] });
  page();
  const panel = await screen.findByRole("region", {
    name: "Waiting for approval",
  });
  expect(panel.textContent).toContain(
    "A change is waiting for approval: version 2",
  );
  expect(panel.textContent).toContain("openai/gpt-6-luna at low");
  expect(panel.textContent).toContain(
    "google/gemini-3.5-flash-lite at minimal",
  );
  const diff = within(panel).getByRole("group", { name: "Changes" });
  expect(within(diff).getByText("Never guess.")).toBeDefined();
  expect(within(diff).getByText("Old line")).toBeDefined();
});

it("approves after a confirmation, and says what it will spend", async () => {
  const fetchStub = stubApi({
    ...common,
    "/api/rounds": [waitingRound],
    "POST /api/rounds/2/approve": {},
  });
  page();
  await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
  const dialog = await screen.findByRole("dialog", {
    name: "Approve version 2?",
  });
  expect(dialog.textContent).toContain("twice on the held-out set");
  expect(dialog.textContent).toContain("$0.38 more");
  expect(
    fetchStub.mock.calls.some(([path]) => path === "/api/rounds/2/approve"),
  ).toBe(false);
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Approve" }),
  );
  await vi.waitFor(() => {
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  expect(fetchStub).toHaveBeenCalledWith(
    "/api/rounds/2/approve",
    expect.objectContaining({ method: "POST" }),
  );
});

it("gives the backend's reason when an approval is refused", async () => {
  stubApi({
    ...common,
    "/api/rounds": [waitingRound],
    "POST /api/rounds/2/approve": new Refusal(409, {
      detail: "A dispatch is already running",
    }),
  });
  page();
  await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
  await userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Approve",
    }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not record the decision: A dispatch is already running",
  );
});

it("needs a reason to reject, and sends it", async () => {
  const fetchStub = stubApi({
    ...common,
    "/api/rounds": [waitingRound],
    "POST /api/rounds/2/reject": {},
  });
  page();
  await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
  const dialog = await screen.findByRole("dialog", {
    name: "Reject version 2?",
  });
  const confirm = within(dialog).getByRole("button", { name: "Reject" });
  expect((confirm as HTMLButtonElement).disabled).toBe(true);
  await userEvent.type(within(dialog).getByRole("textbox"), "Too specific.");
  expect((confirm as HTMLButtonElement).disabled).toBe(false);
  await userEvent.click(confirm);
  await vi.waitFor(() => {
    expect(fetchStub).toHaveBeenCalledWith(
      "/api/rounds/2/reject",
      expect.objectContaining({
        body: JSON.stringify({ reason: "Too specific." }),
      }),
    );
  });
});

it("asks before it asks for a recipe to be approved, with a different promise", async () => {
  stubApi({
    ...common,
    "/api/rounds": [
      {
        ...waitingRound,
        stage: "recipe_waiting",
        pending: {
          ...waitingRound.pending,
          kind: "recipe",
          version: 2,
          reason: "Shorter.",
          model: null,
          reasoning_effort: null,
          previous_model: null,
          previous_reasoning_effort: null,
          diff: [{ kind: "add", text: "1. Read." }],
        },
      },
    ],
  });
  page();
  const panel = await screen.findByRole("region", {
    name: "Waiting for approval",
  });
  expect(panel.textContent).toContain(
    "A new recipe is waiting for approval: recipe version 2",
  );
  expect(panel.textContent).not.toContain("Model before");
  await userEvent.click(within(panel).getByRole("button", { name: "Approve" }));
  expect((await screen.findByRole("dialog")).textContent).toContain(
    "follows this recipe in the rounds after this one",
  );
});

it("tells a handler who can't decide what they can't do and why", async () => {
  stubApi({ ...common, "/api/rounds": [waitingRound] });
  page(marcus);
  expect((await screen.findByRole("note")).textContent).toContain(
    "Marcus Oyelaran can see the rounds but not start, stop or decide on one.",
  );
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Start a round" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop the round" })).toBeNull();
});

it("starts a round after a confirmation that names the spending limit", async () => {
  const fetchStub = stubApi({
    ...common,
    "/api/rounds": [finishedRound],
    "POST /api/rounds": {},
  });
  page();
  await userEvent.click(
    await screen.findByRole("button", { name: "Start a round" }),
  );
  const dialog = await screen.findByRole("dialog", { name: "Start a round?" });
  expect(dialog.textContent).toContain("up to $0.50");
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Start the round" }),
  );
  await vi.waitFor(() => {
    expect(fetchStub).toHaveBeenCalledWith(
      "/api/rounds",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

it("stops an open round after a confirmation", async () => {
  const fetchStub = stubApi({
    ...common,
    "/api/rounds": [waitingRound],
    "POST /api/rounds/2/stop": {},
  });
  page();
  await userEvent.click(
    await screen.findByRole("button", { name: "Stop the round" }),
  );
  const dialog = await screen.findByRole("dialog", {
    name: "Stop this round?",
  });
  expect(dialog.textContent).toContain("The current version stays as it is");
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Stop the round" }),
  );
  await vi.waitFor(() => {
    expect(fetchStub).toHaveBeenCalledWith(
      "/api/rounds/2/stop",
      expect.objectContaining({ method: "POST" }),
    );
  });
  expect(screen.queryByRole("button", { name: "Start a round" })).toBeNull();
});

it("says why a start was refused", async () => {
  stubApi({
    ...common,
    "/api/rounds": [],
    "POST /api/rounds": new Refusal(409, {
      detail: "A dispatch is already running",
    }),
  });
  page();
  await userEvent.click(
    await screen.findByRole("button", { name: "Start a round" }),
  );
  await userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Start the round",
    }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the round: A dispatch is already running",
  );
});

it("lists the rounds with the scores before and after, the verdict, who decided and the cost", async () => {
  stubApi({ ...common, "/api/rounds": [waitingRound, finishedRound] });
  page();
  const table = await screen.findByRole("table", { name: "Rounds" });
  const [, open, done] = within(table).getAllByRole("row");
  expect(open.textContent).toContain("Open");
  expect(done.textContent).toContain("Kept");
  expect(done.textContent).toContain("accuracy improved");
  expect(done.textContent).toContain("64% to 80%");
  expect(done.textContent).toContain("1.40 to 1.70");
  expect(done.textContent).toContain("Approved the change - Dana Whitfield");
  expect(done.textContent).toContain("Rejected the recipe - Dana Whitfield");
  expect(done.textContent).toContain('("Too long.")');
  expect(done.textContent).toContain("$0.2000");
  const cells = within(done).getAllByRole("cell");
  expect(
    cells.filter((c) => c.className.includes("num")).length,
  ).toBeGreaterThan(3);
});

it("charts the held-out accuracy and grade of each version tried", async () => {
  stubApi({
    ...common,
    "/api/rounds": [
      {
        ...finishedRound,
        id: 2,
        tried_version: 3,
        verdict: "rolled_back",
        held_out_before: finishedRound.held_out_after,
        held_out_after: {
          runs: 2,
          accuracy: 0.6,
          grade: 1.1,
          cost_per_claim: 0.001,
          seconds_per_claim: 21,
        },
      },
      finishedRound,
    ],
  });
  page();
  const accuracy = await screen.findByRole("img", {
    name: /Held-out accuracy: version 1 64%, version 2 80%, version 3 60%/,
  });
  expect(accuracy).toBeDefined();
  expect(
    screen.getByRole("img", {
      name: /Held-out grade: version 1 1.40, version 2 1.70, version 3 1.10/,
    }),
  ).toBeDefined();
  expect(screen.getByText(/A circle is a version that was kept/)).toBeDefined();
  expect(document.querySelectorAll(".mark-rolled-back")).toHaveLength(2);
  expect(document.querySelectorAll(".mark-kept")).toHaveLength(4);
});

it("shows the recipe's versions and what a version changed", async () => {
  stubApi({ ...common, "/api/rounds": [finishedRound] });
  page();
  expect(await screen.findByText("What recipe 2 changed")).toBeDefined();
  const diffs = screen.getAllByRole("group", { name: "Changes" });
  expect(within(diffs[diffs.length - 1]).getByText("1. Read.")).toBeDefined();
  await userEvent.click(screen.getByRole("button", { name: "Recipe 1" }));
  expect(await screen.findByText("What recipe 1 changed")).toBeDefined();
  expect(screen.getByText("1. Call read_results.")).toBeDefined();
});

it("shows a loading message, then says what to do when it cannot load", async () => {
  stubApi({});
  page();
  expect(screen.getByRole("status").textContent).toBe("Loading the rounds...");
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Reload the page",
  );
});

it("says so when the recipes cannot load", async () => {
  stubApi({ "/api/rounds": [finishedRound], "/api/settings": settingsView });
  page();
  expect(await screen.findByText("Could not load the recipes.")).toBeDefined();
});

it("follows an open round every 2 seconds and stops once it ends", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let rounds: Round[] = [
    {
      ...waitingRound,
      stage: "evaluating" as const,
      done: 1,
      of: 3,
      pending: null,
    },
  ];
  const fetchStub = stubApi({ ...common, "/api/rounds": () => rounds });
  page();
  await screen.findByText("Evaluations (1 of 3)");
  rounds = [{ ...rounds[0], done: 2 }];
  await act(() => vi.advanceTimersByTimeAsync(2000));
  expect(await screen.findByText("Evaluations (2 of 3)")).toBeDefined();
  rounds = [
    { ...rounds[0], state: "finished" as const, stage: "finished" as const },
  ];
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Round 2");
  const calls = fetchStub.mock.calls.filter(
    ([path]) => path === "/api/rounds",
  ).length;
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(
    fetchStub.mock.calls.filter(([path]) => path === "/api/rounds"),
  ).toHaveLength(calls);
});

it("says who stopped a round and that the current version stayed", async () => {
  stubApi({
    ...common,
    "/api/rounds": [
      {
        ...waitingRound,
        state: "stopped",
        stage: "stopped",
        stopped_by: "Dana Whitfield",
        pending: null,
        why: "The round has spent its limit",
      },
    ],
  });
  page();
  expect(
    await screen.findByText(
      /Stopped by Dana Whitfield. The current version stayed as it was./,
    ),
  ).toBeDefined();
  expect(
    screen.getAllByText("The round has spent its limit").length,
  ).toBeGreaterThan(0);
});
