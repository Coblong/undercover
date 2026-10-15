import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { claim, claimRoutes, stubApi, summaries } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the queue and asks for a claim to be selected", async () => {
  stubApi({ "/api/claims": summaries });
  render(<App />);
  expect(await screen.findByRole("button", { name: /CLM-1001/ })).toBeDefined();
  expect(screen.getByText("Select a claim to see it in full.")).toBeDefined();
});

it("opens a claim from the queue", async () => {
  stubApi({
    "/api/claims": summaries,
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
  stubApi({});
  render(<App />);
  expect(screen.getByRole("status").textContent).toBe("Loading claims...");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load the claims.",
  );
});
