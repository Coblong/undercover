import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { always, getJson, postJson, useApi } from "./api";
import { Refusal, stubApi } from "./fixtures";
import { accessToken, signOut } from "./supabase";

vi.mock("./supabase", () => ({
  accessToken: vi.fn(() => Promise.resolve("the-token")),
  signOut: vi.fn(),
}));

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

it("sends the access token with a read and an action", async () => {
  const fetchStub = stubApi({ "/api/n": { n: 1 }, "POST /api/go": {} });
  await getJson("/api/n");
  await postJson("/api/go", { a: 1 });
  expect(fetchStub).toHaveBeenCalledWith("/api/n", {
    headers: { authorization: "Bearer the-token" },
  });
  expect(fetchStub).toHaveBeenCalledWith(
    "/api/go",
    expect.objectContaining({
      headers: {
        "content-type": "application/json",
        authorization: "Bearer the-token",
      },
    }),
  );
});

it("sends no token when nobody is signed in", async () => {
  vi.mocked(accessToken).mockResolvedValueOnce(null);
  const fetchStub = stubApi({ "/api/n": { n: 1 } });
  await getJson("/api/n");
  expect(fetchStub).toHaveBeenCalledWith("/api/n", { headers: {} });
});

it("signs out when the backend does not accept the sign-in", async () => {
  stubApi({ "/api/n": new Refusal(401, { detail: "Sign in to do that" }) });
  await expect(getJson("/api/n")).rejects.toThrow();
  expect(signOut).toHaveBeenCalled();
  stubApi({
    "POST /api/go": new Refusal(401, { detail: "Sign in to do that" }),
  });
  await expect(postJson("/api/go")).rejects.toThrow("Sign in to do that");
  expect(signOut).toHaveBeenCalledTimes(2);
});
