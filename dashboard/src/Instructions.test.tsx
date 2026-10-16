import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Instructions } from "./Instructions";
import { stubApi } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the instructions and offers no way to change them", async () => {
  stubApi({ "/api/instructions": { text: "You are the UnderCover Agent." } });
  render(<Instructions />);
  expect(
    await screen.findByText("You are the UnderCover Agent."),
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
