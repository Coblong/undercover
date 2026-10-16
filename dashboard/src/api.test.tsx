import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { always, useApi } from "./api";
import { stubApi } from "./fixtures";

afterEach(() => {
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
