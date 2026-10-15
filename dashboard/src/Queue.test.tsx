import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { summaries } from "./fixtures";
import { Queue } from "./Queue";

afterEach(cleanup);

it("counts the claims in each state", () => {
  render(<Queue claims={summaries} selected={null} onSelect={vi.fn()} />);
  const counts = screen.getAllByRole("listitem").slice(0, 4);
  expect(counts.map((item) => item.textContent)).toEqual([
    "1Waiting",
    "0In progress",
    "1Waiting for sign-off",
    "1Decided",
  ]);
});

it("lists each claim with its summary, customer and amount", () => {
  render(<Queue claims={summaries} selected={null} onSelect={vi.fn()} />);
  const row = screen.getByRole("button", { name: /CLM-1002/ });
  expect(
    within(row).getByText("Suitcase lost on a connecting flight"),
  ).toBeDefined();
  expect(within(row).getByText("$1,950.00")).toBeDefined();
  expect(within(row).getByText("Waiting for sign-off")).toBeDefined();
  expect(within(row).getByText(/Tom Becker, baggage/)).toBeDefined();
});

it("selects a claim when its row is clicked", async () => {
  const onSelect = vi.fn();
  render(<Queue claims={summaries} selected="CLM-1001" onSelect={onSelect} />);
  expect(
    screen
      .getByRole("button", { name: /CLM-1001/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await userEvent.click(screen.getByRole("button", { name: /CLM-1003/ }));
  expect(onSelect).toHaveBeenCalledWith("CLM-1003");
});
