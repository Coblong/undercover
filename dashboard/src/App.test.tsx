import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { claim, claimRoutes, stubApi, summaries } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the queue and asks for a claim to be selected", async () => {
  stubApi({ "/api/claims": summaries, "/api/runs/latest": null });
  render(<App />);
  expect(await screen.findByRole("button", { name: /CLM-1001/ })).toBeDefined();
  expect(screen.getByText("Select a claim to see it in full.")).toBeDefined();
});

it("opens a claim from the queue", async () => {
  stubApi({
    "/api/claims": summaries,
    "/api/runs/latest": null,
    "/api/claims/CLM-1001": claim,
    ...claimRoutes,
  });
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: /CLM-1001/ }),
  );
  expect(
    await screen.findByRole("article", { name: "Claim CLM-1001" }),
  ).toBeDefined();
});

it("shows a loading message, then an error when the queue cannot be loaded", async () => {
  stubApi({ "/api/runs/latest": null });
  render(<App />);
  expect(screen.getByRole("status").textContent).toBe("Loading claims...");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load the claims.",
  );
});

it("switches to the instructions and back", async () => {
  stubApi({
    "/api/claims": summaries,
    "/api/runs/latest": null,
    "/api/instructions/queue": { text: "Plan the queue." },
    "/api/instructions/claim": { text: "Decide from the wording." },
  });
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Instructions" }));
  expect(await screen.findByText("Decide from the wording.")).toBeDefined();
  expect(screen.queryByRole("button", { name: /CLM-1001/ })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Claims" }));
  expect(await screen.findByRole("button", { name: /CLM-1001/ })).toBeDefined();
});

it("keeps the queue up to date", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let queue = summaries;
  stubApi({ "/api/claims": () => queue, "/api/runs/latest": null });
  render(<App />);
  await screen.findByRole("button", { name: /CLM-1001/ });
  const decided = () => screen.getAllByRole("listitem")[3]?.textContent;
  expect(decided()).toBe("1Decided");
  queue = summaries.map((c) => ({ ...c, state: "decided" as const }));
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await vi.waitFor(() => {
    expect(decided()).toBe("3Decided");
  });
  vi.useRealTimers();
});
