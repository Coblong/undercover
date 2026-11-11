import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import {
  claim,
  claimRoutes,
  dana,
  evaluationCopy,
  finishedEvaluation,
  finishedEvaluationRow,
  stubApi,
  summaries,
} from "./fixtures";

vi.mock("./supabase", () => ({
  accessToken: () => Promise.resolve(null),
  signIn: vi.fn(),
  signOut: vi.fn(),
  watchSignedIn: (onChange: (signedIn: boolean) => void) => {
    onChange(true);
    return () => undefined;
  },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const me = { "/api/me": dana };

it("shows the queue and asks for a claim to be selected", async () => {
  stubApi({ ...me, "/api/claims": summaries, "/api/dispatches/latest": null });
  render(<App />);
  expect(await screen.findByRole("button", { name: /CLM-1001/ })).toBeDefined();
  expect(screen.getByText("Signed in as")).toBeDefined();
  expect(screen.getByText("Dana Whitfield")).toBeDefined();
  expect(
    screen.getByRole("heading", { name: "Claims", level: 2 }),
  ).toBeDefined();
  expect(screen.getByText("Select a claim to see it in full.")).toBeDefined();
});

it("opens a claim from the queue", async () => {
  stubApi({
    ...me,
    "/api/claims": summaries,
    "/api/dispatches/latest": null,
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

it("shows a loading message while the queue loads", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      path === "/api/me"
        ? Promise.resolve(new Response(JSON.stringify(dana)))
        : new Promise<Response>(() => undefined),
    ),
  );
  render(<App />);
  expect(await screen.findByText("Loading claims...")).toBeDefined();
});

it("says so when the queue cannot be loaded", async () => {
  stubApi({ ...me, "/api/dispatches/latest": null });
  render(<App />);
  expect(
    (await screen.findByText("Could not load the claims.")).textContent,
  ).toBe("Could not load the claims.");
});

it("switches to the instructions and back", async () => {
  stubApi({
    ...me,
    "/api/claims": summaries,
    "/api/dispatches/latest": null,
    "/api/instructions/claim": { text: "Decide from the wording." },
  });
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Instructions" }),
  );
  expect(await screen.findByText("Decide from the wording.")).toBeDefined();
  expect(screen.queryByRole("button", { name: /CLM-1001/ })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Claims" }));
  expect(await screen.findByRole("button", { name: /CLM-1001/ })).toBeDefined();
});

it("keeps the queue up to date", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let queue = summaries;
  stubApi({
    ...me,
    "/api/claims": () => queue,
    "/api/dispatches/latest": null,
  });
  render(<App />);
  await screen.findByRole("button", { name: /CLM-1001/ });
  const decided = () => screen.getAllByRole("listitem")[3]?.textContent;
  expect(decided()).toBe("1Decided");
  queue = summaries.map((c) => ({ ...c, state: "decided" as const }));
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await vi.waitFor(() => {
    expect(decided()).toBe("3Decided");
  });
  vi.useRealTimers();
});

it("opens an evaluation's claim from the scoreboard in the claims view", async () => {
  stubApi({
    ...me,
    "/api/claims": summaries,
    "/api/dispatches/latest": null,
    "/api/evaluations": [finishedEvaluationRow],
    "/api/evaluations/3": finishedEvaluation,
    "/api/claims/EV3-CLM-1114": evaluationCopy,
  });
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Scoreboard" }),
  );
  await userEvent.click(await screen.findByRole("button", { name: "3" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "EV3-CLM-1114" }),
  );
  expect(
    await screen.findByRole("article", { name: "Claim EV3-CLM-1114" }),
  ).toBeDefined();
  expect(screen.getByRole("region", { name: "Queue" })).toBeDefined();
  expect(screen.queryByRole("region", { name: "Scoreboard" })).toBeNull();
});
