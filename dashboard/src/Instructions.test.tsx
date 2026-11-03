import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Instructions } from "./Instructions";
import { stubApi } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the claim agent's instructions and offers no way to change them", async () => {
  stubApi({
    "/api/instructions/claim": { text: "Decide from the wording." },
  });
  render(<Instructions />);
  expect(await screen.findByText("Decide from the wording.")).toBeDefined();
  expect(
    screen.queryByRole("heading", { name: "Running the queue" }),
  ).toBeNull();
  expect(
    screen.getByRole("heading", { name: "Working a claim" }),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});

it("says so when the instructions cannot be loaded", async () => {
  stubApi({});
  render(<Instructions />);
  expect(screen.getByRole("status").textContent).toBe("Loading...");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load the instructions.",
  );
});
