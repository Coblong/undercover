import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Instructions } from "./Instructions";
import { stubApi } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows both sets of instructions and offers no way to change them", async () => {
  stubApi({
    "/api/instructions/queue": { text: "Plan the queue." },
    "/api/instructions/claim": { text: "Decide from the wording." },
  });
  render(<Instructions />);
  expect(await screen.findByText("Plan the queue.")).toBeDefined();
  expect(await screen.findByText("Decide from the wording.")).toBeDefined();
  expect(
    screen.getByRole("heading", { name: "Running the queue" }),
  ).toBeDefined();
  expect(
    screen.getByRole("heading", { name: "Working a claim" }),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});

it("says so when the instructions cannot be loaded", async () => {
  stubApi({});
  render(<Instructions />);
  expect(screen.getAllByRole("status")[0].textContent).toBe("Loading...");
  expect((await screen.findAllByRole("alert"))[0].textContent).toBe(
    "Could not load the instructions.",
  );
});
