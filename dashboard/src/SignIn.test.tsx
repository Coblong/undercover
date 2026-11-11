import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { stubApi } from "./fixtures";
import { SignIn } from "./SignIn";
import { signIn, signOut } from "./supabase";

let signedIn = false;

vi.mock("./supabase", () => ({
  accessToken: () => Promise.resolve(null),
  signIn: vi.fn(),
  signOut: vi.fn(),
  watchSignedIn: (onChange: (signedIn: boolean) => void) => {
    onChange(signedIn);
    return () => undefined;
  },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(signIn).mockReset();
  vi.mocked(signOut).mockReset();
  signedIn = false;
});

it("asks for sign-in before showing anything else", () => {
  render(<App />);
  expect(screen.getByRole("form", { name: "Sign in" })).toBeDefined();
  expect(screen.queryByRole("navigation")).toBeNull();
});

it("signs in with the email address and password", async () => {
  vi.mocked(signIn).mockResolvedValue();
  render(<SignIn />);
  await userEvent.type(
    screen.getByLabelText("Email address"),
    "dana.whitfield@undercover.example",
  );
  await userEvent.type(screen.getByLabelText("Password"), "secret");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(signIn).toHaveBeenCalledWith(
    "dana.whitfield@undercover.example",
    "secret",
  );
  expect(screen.queryByRole("alert")).toBeNull();
});

it("says so when the email address and password do not match", async () => {
  vi.mocked(signIn).mockRejectedValue(
    new Error("That email address and password do not match"),
  );
  render(<SignIn />);
  await userEvent.type(screen.getByLabelText("Email address"), "a@b.example");
  await userEvent.type(screen.getByLabelText("Password"), "wrong");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "That email address and password do not match",
  );
});

it("keeps to a plain message when sign-in fails some other way", async () => {
  vi.mocked(signIn).mockRejectedValue("offline");
  render(<SignIn />);
  await userEvent.type(screen.getByLabelText("Email address"), "a@b.example");
  await userEvent.type(screen.getByLabelText("Password"), "x");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not sign in",
  );
});

it("turns away a sign-in that is not a handler, and offers sign out", async () => {
  signedIn = true;
  stubApi({});
  render(<App />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "not one of UnderCover's claims handlers",
  );
  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(signOut).toHaveBeenCalled();
});
