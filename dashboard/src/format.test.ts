import { expect, it } from "vitest";
import {
  date,
  duration,
  modelCost,
  money,
  timeOfDay,
  uniqueKeys,
  words,
} from "./format";

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

it("keeps keys unique when a text repeats", () => {
  expect(uniqueKeys(["a", "b", "a", "a"])).toEqual([
    "a",
    "b",
    "a (2)",
    "a (3)",
  ]);
  expect(uniqueKeys([])).toEqual([]);
});

it("shows a model cost to four places", () => {
  expect(modelCost(0.0123456)).toBe("$0.0123");
  expect(modelCost(0)).toBe("$0.0000");
});

it("shows how long something took", () => {
  expect(duration(4.25)).toBe("4.3 s");
  expect(duration(59.94)).toBe("59.9 s");
  expect(duration(125)).toBe("2 min 5 s");
});

it("shows the time of day in UTC", () => {
  expect(timeOfDay("2026-11-03T10:00:05+00:00")).toBe("10:00:05");
  expect(timeOfDay("2026-11-03T23:59:59+02:00")).toBe("21:59:59");
});
