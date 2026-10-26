import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { always, useApi } from "./api";
import { stubApi } from "./fixtures";

afterEach(() => {
  hide(false);
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function Probe({ path }: Readonly<{ path: string | null }>) {
  const { data, failed } = useApi<{ n: number }>(path, always);
  return <p>{failed ? "failed" : String(data?.n ?? "none")}</p>;
}

it("loads nothing for a null path", () => {
  const fetchStub = stubApi({});
  render(<Probe path={null} />);
  expect(screen.getByText("none")).toBeDefined();
  expect(fetchStub).not.toHaveBeenCalled();
});

it("keeps trying after a failure and recovers", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  stubApi({});
  render(<Probe path="/api/n" />);
  expect(await screen.findByText("failed")).toBeDefined();
  stubApi({ "/api/n": { n: 7 } });
  await act(() => vi.advanceTimersByTimeAsync(4000));
  expect(await screen.findByText("7")).toBeDefined();
});

function hide(hidden: boolean) {
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: hidden,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

it("stops polling while the tab is hidden and loads again when it returns", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let n = 1;
  const fetchStub = stubApi({ "/api/n": () => ({ n: n++ }) });
  render(<Probe path="/api/n" />);
  expect(await screen.findByText("1")).toBeDefined();
  hide(true);
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(fetchStub).toHaveBeenCalledTimes(1);
  hide(false);
  expect(await screen.findByText("2")).toBeDefined();
  await act(() => vi.advanceTimersByTimeAsync(2000));
  expect(await screen.findByText("3")).toBeDefined();
});

it("stops listening for the tab when it is no longer shown", async () => {
  const remove = vi.spyOn(document, "removeEventListener");
  stubApi({ "/api/n": { n: 1 } });
  const { unmount } = render(<Probe path="/api/n" />);
  expect(await screen.findByText("1")).toBeDefined();
  unmount();
  expect(remove).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
  remove.mockRestore();
});
