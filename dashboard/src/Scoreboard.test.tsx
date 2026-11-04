import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  finishedEvaluation,
  finishedEvaluationRow,
  Refusal,
  stubApi,
} from "./fixtures";
import { Scoreboard } from "./Scoreboard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const running = { ...finishedEvaluationRow, state: "running" as const };

it("starts an evaluation of a set, whole or tiny, with the number chosen", async () => {
  let list: unknown[] = [];
  const fetchStub = stubApi({
    "/api/evaluations": () => list,
    "POST /api/evaluations": () => {
      list = [running];
      return running;
    },
  });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  const agents = screen.getByLabelText("Claim agents at once");
  expect((agents as HTMLInputElement).value).toBe("3");
  await userEvent.selectOptions(screen.getByLabelText("Set"), "held_out");
  await userEvent.click(screen.getByLabelText("Just the 5 tiny claims"));
  await userEvent.clear(agents);
  await userEvent.type(agents, "5");
  await userEvent.click(
    screen.getByRole("button", { name: "Start an evaluation" }),
  );
  expect(await screen.findByText("Running")).toBeDefined();
  expect(fetchStub).toHaveBeenCalledWith(
    "/api/evaluations",
    expect.objectContaining({
      body: JSON.stringify({
        concurrency: 5,
        eval_set: "held_out",
        tiny: true,
      }),
    }),
  );
  expect(
    screen
      .getByRole("button", { name: "Start an evaluation" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

it("keeps the number of claim agents between 1 and 25", async () => {
  stubApi({ "/api/evaluations": [] });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  const agents = screen.getByLabelText("Claim agents at once");
  const start = screen.getByRole("button", { name: "Start an evaluation" });
  for (const bad of ["0", "26", "1.5"]) {
    await userEvent.clear(agents);
    await userEvent.type(agents, bad);
    expect(start.hasAttribute("disabled")).toBe(true);
  }
  expect(await screen.findByText("No evaluations yet.")).toBeDefined();
});

it("shows the backend's reason when a start is refused", async () => {
  stubApi({
    "/api/evaluations": [],
    "POST /api/evaluations": new Refusal(409, {
      detail: "A dispatch or an evaluation is already running",
    }),
  });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Start an evaluation" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the evaluation: A dispatch or an evaluation is already running",
  );
});

it("lists the evaluations with every measure", async () => {
  stubApi({ "/api/evaluations": [finishedEvaluationRow] });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  const table = await screen.findByRole("table", { name: "Evaluations" });
  const row = within(table).getAllByRole("row")[1];
  expect(row.textContent).toBe(
    "3Held-out set25openai/gpt-6-lunalowFinished" +
      "50%50%$1,250.00100%$0.012331.5 s1 min 30 s",
  );
});

it("shows no run time while it runs", async () => {
  stubApi({
    "/api/evaluations": [
      {
        ...running,
        measures: { ...running.measures, run_seconds: null },
      },
    ],
  });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  const row = within(
    await screen.findByRole("table", { name: "Evaluations" }),
  ).getAllByRole("row")[1];
  expect(row.textContent.endsWith("-")).toBe(true);
});

it("opens an evaluation to its claims, marks a miss, and links to a claim", async () => {
  stubApi({
    "/api/evaluations": [finishedEvaluationRow],
    "/api/evaluations/3": finishedEvaluation,
  });
  const onOpenClaim = vi.fn();
  render(<Scoreboard onOpenClaim={onOpenClaim} />);
  await userEvent.click(await screen.findByRole("button", { name: "3" }));
  const table = await screen.findByRole("table", { name: "Evaluation claims" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows.map((r) => r.textContent)).toEqual([
    "EV3-CLM-1104CLM-1104Approved $250.00Approved $250.00Match$0.00$0.010030.0 s",
    "EV3-CLM-1114CLM-1114DeclinedApproved $1,250.00Miss$1,250.00$0.014633.0 s",
  ]);
  expect(rows[0].className).toBe("");
  expect(rows[1].className).toBe("miss");
  await userEvent.click(
    within(table).getByRole("button", { name: "EV3-CLM-1114" }),
  );
  expect(onOpenClaim).toHaveBeenCalledWith("EV3-CLM-1114");
});

it("shows a claim the agent did not decide", async () => {
  const [first] = finishedEvaluation.results;
  stubApi({
    "/api/evaluations": [finishedEvaluationRow],
    "/api/evaluations/3": {
      ...finishedEvaluation,
      results: [
        { ...first, agent_decision: null, agent_payout: null, right: false },
      ],
    },
  });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "3" }));
  expect(await screen.findByText("Not decided")).toBeDefined();
});

it("says so when an evaluation or the list cannot be loaded", async () => {
  stubApi({ "/api/evaluations": [finishedEvaluationRow] });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "3" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load this evaluation.",
  );
  cleanup();
  stubApi({});
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load the evaluations.",
  );
});

it("refreshes every 2 seconds while one runs and stops when it ends", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let list: unknown[] = [running];
  const fetchStub = stubApi({ "/api/evaluations": () => list });
  render(<Scoreboard onOpenClaim={vi.fn()} />);
  await screen.findByText("Running");
  list = [finishedEvaluationRow];
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Finished");
  const calls = fetchStub.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(6000));
  expect(fetchStub.mock.calls).toHaveLength(calls);
});
