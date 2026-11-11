import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { RunAgent, SignOffForm } from "./Actions";
import { dana, marcus, Refusal, stubApi } from "./fixtures";
import { MeContext } from "./Me";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("starts the agent and says when it is done", async () => {
  const fetchStub = stubApi({ "POST /api/claims/CLM-1001/run": {} });
  const onDone = vi.fn();
  render(<RunAgent claimId="CLM-1001" onDone={onDone} />);
  await userEvent.click(screen.getByRole("button", { name: "Run agent" }));
  await vi.waitFor(() => {
    expect(onDone).toHaveBeenCalled();
  });
  expect(fetchStub).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/run",
    expect.objectContaining({ method: "POST" }),
  );
});

it("says so when the agent cannot be started", async () => {
  stubApi({});
  const onDone = vi.fn();
  render(<RunAgent claimId="CLM-1001" onDone={onDone} />);
  await userEvent.click(screen.getByRole("button", { name: "Run agent" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the agent: The request failed (404)",
  );
  expect(onDone).not.toHaveBeenCalled();
});

it("gives the backend's reason when the agent cannot be started", async () => {
  stubApi({
    "POST /api/claims/CLM-1001/run": new Refusal(409, {
      detail: "Claim CLM-1001 is decided, so the agent cannot start on it",
    }),
  });
  render(<RunAgent claimId="CLM-1001" onDone={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Run agent" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the agent: Claim CLM-1001 is decided, so the agent cannot start on it",
  );
});

it("gives the messages of a refused request in a list", async () => {
  stubApi({
    "POST /api/claims/CLM-1001/run": new Refusal(422, {
      detail: [{ msg: "Input should be finite" }, { msg: "Field required" }],
    }),
  });
  render(<RunAgent claimId="CLM-1001" onDone={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Run agent" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the agent: Input should be finite, Field required",
  );
});

it("keeps to a plain message when the network fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
  );
  render(<RunAgent claimId="CLM-1001" onDone={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Run agent" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not start the agent: Something went wrong",
  );
});

it("signs off as the signed-in handler", async () => {
  const fetchStub = stubApi({ "POST /api/claims/CLM-1001/sign-off": {} });
  const onDone = vi.fn();
  render(
    <MeContext value={dana}>
      <SignOffForm claimId="CLM-1001" payout={1250} onDone={onDone} />
    </MeContext>,
  );
  expect(
    screen.getByText("You will sign off as Dana Whitfield."),
  ).toBeDefined();
  await userEvent.click(
    screen.getByRole("button", { name: "Sign off $1,250.00" }),
  );
  await vi.waitFor(() => {
    expect(onDone).toHaveBeenCalled();
  });
  expect(fetchStub).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/sign-off",
    expect.objectContaining({ method: "POST", body: undefined }),
  );
});

it("says what a handler can't do and why, and offers no sign-off", () => {
  stubApi({});
  render(
    <MeContext value={marcus}>
      <SignOffForm claimId="CLM-1001" payout={1250} onDone={vi.fn()} />
    </MeContext>,
  );
  expect(
    screen.getByText(/Marcus Oyelaran cannot sign off payouts over the limit/),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: /Sign off/ })).toBeNull();
});

it("gives the backend's reason when the sign-off is refused", async () => {
  stubApi({
    "POST /api/claims/CLM-1001/sign-off": new Refusal(409, {
      detail: "Claim CLM-1001 is decided, not waiting for sign-off",
    }),
  });
  render(
    <MeContext value={dana}>
      <SignOffForm claimId="CLM-1001" payout={1250} onDone={vi.fn()} />
    </MeContext>,
  );
  await userEvent.click(screen.getByRole("button", { name: /Sign off/ }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not sign off this claim: Claim CLM-1001 is decided, not waiting for sign-off",
  );
});
