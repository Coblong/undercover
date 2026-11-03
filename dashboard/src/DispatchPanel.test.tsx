import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DispatchPanel } from "./DispatchPanel";
import {
  finishedDispatch,
  Refusal,
  runningDispatch,
  stubApi,
} from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("offers 3 claim agents to start with, from 1 to 25", async () => {
  stubApi({ "/api/dispatches/latest": null });
  render(<DispatchPanel />);
  const agents = screen.getByLabelText("Claim agents at once");
  expect((agents as HTMLInputElement).value).toBe("3");
  expect(agents.getAttribute("min")).toBe("1");
  expect(agents.getAttribute("max")).toBe("25");
  const start = screen.getByRole("button", { name: "Start a dispatch" });
  for (const bad of ["0", "26", "2.5", ""]) {
    await userEvent.clear(agents);
    if (bad) await userEvent.type(agents, bad);
    expect(start.hasAttribute("disabled")).toBe(true);
  }
  await userEvent.clear(agents);
  await userEvent.type(agents, "25");
  expect(start.hasAttribute("disabled")).toBe(false);
});

it("starts a dispatch with the number chosen and shows it", async () => {
  let latest: unknown = null;
  const fetchStub = stubApi({
    "/api/dispatches/latest": () => latest,
    "POST /api/dispatches": () => {
      latest = runningDispatch;
      return runningDispatch;
    },
  });
  render(<DispatchPanel />);
  const agents = screen.getByLabelText("Claim agents at once");
  await userEvent.clear(agents);
  await userEvent.type(agents, "7");
  await userEvent.click(
    screen.getByRole("button", { name: "Start a dispatch" }),
  );
  expect(await screen.findByText("Running")).toBeDefined();
  expect(fetchStub).toHaveBeenCalledWith(
    "/api/dispatches",
    expect.objectContaining({ body: JSON.stringify({ concurrency: 7 }) }),
  );
  expect(
    screen
      .getByRole("button", { name: "Start a dispatch" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

it("shows the backend's reason when the start is refused", async () => {
  stubApi({
    "/api/dispatches/latest": null,
    "POST /api/dispatches": new Refusal(503, {
      detail: "The agent's service could not be reached.",
    }),
  });
  render(<DispatchPanel />);
  await userEvent.click(
    screen.getByRole("button", { name: "Start a dispatch" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the dispatch: The agent's service could not be reached.",
  );
});

it("lists each claim with where it stands, its time and its cost", async () => {
  stubApi({ "/api/dispatches/latest": runningDispatch });
  render(<DispatchPanel />);
  const table = await screen.findByRole("table", { name: "Dispatch claims" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows.map((row) => row.textContent)).toEqual([
    "CLM-1001DecidedApproved $420.0010:00:0142.5 s$0.0123",
    "CLM-1002In progressNot decided10:00:0212.0 s$0.0044",
    "CLM-1003WaitingNot decidedNot yet0.0 s$0.0000",
  ]);
  expect(screen.getByText("$0.0431")).toBeDefined();
  expect(screen.getByText("1 min 15 s")).toBeDefined();
  expect(screen.getByText("2 min 10 s")).toBeDefined();
  expect(screen.queryByLabelText("Summary")).toBeNull();
});

it("shows the summary built from the records when the dispatch ends", async () => {
  stubApi({ "/api/dispatches/latest": finishedDispatch });
  render(<DispatchPanel />);
  const summary = await screen.findByLabelText("Summary");
  expect(summary.textContent).toBe(
    "2 approved, 1 declined, 1 referred." +
      "Waiting for a sign-off: CLM-1003 $3,750.00." +
      "Left for a handler: CLM-1005." +
      "Still in progress: CLM-1006.",
  );
  expect(screen.getByText("Finished")).toBeDefined();
  expect(
    screen
      .getByRole("button", { name: "Start a dispatch" })
      .hasAttribute("disabled"),
  ).toBe(false);
});

it("leaves out what the summary has nothing to say about", async () => {
  stubApi({
    "/api/dispatches/latest": {
      ...finishedDispatch,
      summary: {
        approved: 0,
        declined: 0,
        referred: 0,
        waiting_for_sign_off: [],
        left_for_handler: [],
        in_progress: [],
      },
    },
  });
  render(<DispatchPanel />);
  expect((await screen.findByLabelText("Summary")).textContent).toBe(
    "0 approved, 0 declined, 0 referred.",
  );
});

it("refreshes every 2 seconds while it runs and stops when it ends", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let latest = runningDispatch;
  const fetchStub = stubApi({ "/api/dispatches/latest": () => latest });
  render(<DispatchPanel />);
  await screen.findByText("Running");
  latest = finishedDispatch;
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Finished");
  const calls = fetchStub.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(6000));
  expect(fetchStub.mock.calls).toHaveLength(calls);
});

it("says so while the claims are being chosen, and when it cannot load", async () => {
  stubApi({ "/api/dispatches/latest": { ...runningDispatch, claims: [] } });
  render(<DispatchPanel />);
  expect(await screen.findByText("Choosing the claims to work.")).toBeDefined();
  cleanup();
  stubApi({});
  render(<DispatchPanel />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load the latest dispatch.",
  );
});
