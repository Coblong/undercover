import { expect, it, vi } from "vitest";
import { accessToken, signIn, signOut, watchSignedIn } from "./supabase";

// The tests set no Supabase address, so the client is never built.

it("says sign-in is not set up when there is no address", async () => {
  await expect(signIn("a@b.example", "x")).rejects.toThrow(
    "Sign-in is not set up",
  );
});

it("has no token and nobody signed in without one", async () => {
  expect(await accessToken()).toBeNull();
  await signOut();
  const onChange = vi.fn();
  const stop = watchSignedIn(onChange);
  expect(onChange).toHaveBeenCalledWith(false);
  stop();
});
