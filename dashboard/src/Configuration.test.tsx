import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Configuration } from "./Configuration";
import { stubApi, versionOne, versionTwo } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the current version and every version", async () => {
  stubApi({ "/api/configurations": [versionTwo, versionOne] });
  render(<Configuration />);
  const current = await screen.findByRole("region", {
    name: "Current version",
  });
  expect(current.textContent).toContain("google/gemini-3.5-flash-lite");
  expect(current.textContent).toContain("Version 2");
  const versions = screen.getByRole("region", { name: "Versions" });
  expect(within(versions).getAllByRole("row")).toHaveLength(3);
  expect(within(versions).getByText("Current")).toBeDefined();
  expect(within(versions).getByText("Earlier")).toBeDefined();
  // Version 1 has two held-out runs, with an accuracy of 64% and a grade of 1.40.
  const [, , first] = within(versions).getAllByRole("row");
  expect(first.textContent).toContain("64%");
  expect(first.textContent).toContain("1.40");
  expect(first.textContent.endsWith("2")).toBe(true);
  // Version 2 has no scores yet.
  const [, second] = within(versions).getAllByRole("row");
  expect(second.textContent.endsWith("-0")).toBe(true);
});

it("shows what the chosen version changed, with additions and removals marked", async () => {
  stubApi({ "/api/configurations": [versionTwo, versionOne] });
  render(<Configuration />);
  const panel = await screen.findByRole("region", { name: "Version 2" });
  expect(panel.textContent).toContain(
    "Model: openai/gpt-6-luna at low to google/gemini-3.5-flash-lite at minimal",
  );
  expect(panel.textContent).toContain(
    "Proposed by Improver, round 1, approved by Dana Whitfield on Nov 2, 2026",
  );
  const diff = within(panel).getByRole("group", { name: "Changes" });
  expect(within(diff).getByText("Never guess.")).toBeDefined();
  expect(within(diff).getByText("Added")).toBeDefined();
  expect(within(diff).getByText("Removed")).toBeDefined();

  await userEvent.click(screen.getByRole("button", { name: "1" }));
  const first = await screen.findByRole("region", { name: "Version 1" });
  expect(first.textContent).toContain("This is the first version.");
  expect(first.textContent).toContain("Proposed by Seed");
});

it("says so when a version changed no text", async () => {
  stubApi({
    "/api/configurations": [{ ...versionTwo, diff: [] }, versionOne],
  });
  render(<Configuration />);
  expect(await screen.findByText("Nothing changed in the text.")).toBeDefined();
});

it("shows a loading message, then says what to do when it cannot load", async () => {
  stubApi({});
  render(<Configuration />);
  expect(screen.getByRole("status").textContent).toBe(
    "Loading the configuration...",
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Reload the page",
  );
});
