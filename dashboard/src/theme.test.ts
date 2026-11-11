import { afterEach, expect, it, vi } from "vitest";
import { getAppearance, setAppearance } from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

it("defaults to the computer's setting and remembers a choice", () => {
  expect(getAppearance()).toBe("system");
  setAppearance("dark");
  expect(getAppearance()).toBe("dark");
  localStorage.setItem("undercover-appearance", "purple");
  expect(getAppearance()).toBe("system");
});

it("still shows the choice when the browser won't keep it", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  setAppearance("light");
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(getAppearance()).toBe("system");
});
