import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { finishedRun, Refusal, runningRun, stubApi } from "./fixtures";
import { RunPanel } from "./RunPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("shows the to-do list with the state of each item", async () => {
  stubApi({ "/api/runs/latest": runningRun });
  render(<RunPanel waiting={20} />);
  const list = await screen.findByRole("list", { name: "To-do list" });
  expect(
    within(list)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual([
    "DoneCLM-1003 passport",
    "In progressCLM-1001 delay",
    "To doCLM-1002 baggage",
    "Left for a handlerCLM-1004 medical",
  ]);
  expect(screen.getByText("Running")).toBeDefined();
  expect(screen.getByText("5")).toBeDefined();
});

it("shows the summary once the run has finished", async () => {
  stubApi({ "/api/runs/latest": finishedRun });
  render(<RunPanel waiting={20} />);
  expect(
    await screen.findByText("Three claims decided. One is left for a handler."),
  ).toBeDefined();
  expect(screen.getByText("Finished")).toBeDefined();
});

it("says the agent is planning before it has a to-do list", async () => {
  stubApi({ "/api/runs/latest": { ...runningRun, todos: [] } });
  render(<RunPanel waiting={20} />);
  expect(
    await screen.findByText("The agent is planning its work."),
  ).toBeDefined();
});

it("keeps refreshing while the run goes on and stops when it ends", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let run = runningRun;
  const fetchStub = stubApi({ "/api/runs/latest": () => run });
  render(<RunPanel waiting={20} />);
  expect(await screen.findByText("Running")).toBeDefined();
  run = finishedRun;
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Finished");
  const loads = fetchStub.mock.calls.length;
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(fetchStub.mock.calls).toHaveLength(loads);
});

it("has nothing to show before the first run, and the form is ready", async () => {
  const fetchStub = stubApi({ "/api/runs/latest": null });
  render(<RunPanel waiting={25} />);
  await vi.waitFor(() => {
    expect(fetchStub).toHaveBeenCalled();
  });
  expect(screen.queryByRole("list", { name: "To-do list" })).toBeNull();
  expect(screen.getByLabelText<HTMLInputElement>("Claims to work").value).toBe(
    "25",
  );
});

it("starts a run on the number of claims chosen", async () => {
  const fetchStub = stubApi({
    "/api/runs/latest": null,
    "POST /api/runs": runningRun,
  });
  render(<RunPanel waiting={25} />);
  const input = screen.getByLabelText("Claims to work");
  await userEvent.clear(input);
  await userEvent.type(input, "5");
  await userEvent.click(screen.getByRole("button", { name: "Run the queue" }));
  const post = fetchStub.mock.calls.find(([, init]) => init?.method === "POST");
  expect(post?.[0]).toBe("/api/runs");
  expect(post?.[1]?.body).toBe(JSON.stringify({ limit: 5 }));
});

it("says so when the run cannot be started", async () => {
  stubApi({ "/api/runs/latest": null });
  render(<RunPanel waiting={25} />);
  await userEvent.click(screen.getByRole("button", { name: "Run the queue" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the run: The request failed (404)",
  );
});

it("gives the backend's reason when the run cannot be started", async () => {
  stubApi({
    "/api/runs/latest": null,
    "POST /api/runs": new Refusal(409, { detail: "A run is already going" }),
  });
  render(<RunPanel waiting={25} />);
  await userEvent.click(screen.getByRole("button", { name: "Run the queue" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the run: A run is already going",
  );
});

it("shows two to-do items with the same text without a key warning", async () => {
  const warn = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const twice = {
    ...runningRun,
    todos: [
      { text: "CLM-1001 delay", status: "done" as const },
      { text: "CLM-1001 delay", status: "to_do" as const },
    ],
  };
  stubApi({ "/api/runs/latest": twice });
  render(<RunPanel waiting={20} />);
  const list = await screen.findByRole("list", { name: "To-do list" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  expect(warn).not.toHaveBeenCalled();
  warn.mockRestore();
});

it("does not offer a run while one is going, with nothing waiting or with no number", async () => {
  stubApi({ "/api/runs/latest": runningRun });
  const { rerender } = render(<RunPanel waiting={20} />);
  await screen.findByText("Running");
  const button = screen.getByRole<HTMLButtonElement>("button", {
    name: "Run the queue",
  });
  expect(button.disabled).toBe(true);

  stubApi({ "/api/runs/latest": finishedRun });
  rerender(<RunPanel waiting={0} />);
  expect(button.disabled).toBe(true);

  rerender(<RunPanel waiting={20} />);
  await userEvent.clear(screen.getByLabelText("Claims to work"));
  expect(button.disabled).toBe(true);
});

it("says so when the latest run cannot be loaded", async () => {
  stubApi({});
  render(<RunPanel waiting={20} />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load the latest run.",
  );
});
