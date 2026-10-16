import { expect, it } from "vitest";
import { date, money, words } from "./format";

it("formats money in US dollars", () => {
  expect(money(1250)).toBe("$1,250.00");
  expect(money(0.5)).toBe("$0.50");
});

it("formats dates and times as dates", () => {
  expect(date("2026-06-18")).toBe("Jun 18, 2026");
  expect(date("2026-10-15T23:59:00+00:00")).toBe("Oct 15, 2026");
});

it("turns identifiers into words", () => {
  expect(words("trip_delay")).toBe("Trip delay");
  expect(words("premium")).toBe("Premium");
});
