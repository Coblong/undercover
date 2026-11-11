import {
  type APIRequestContext,
  expect,
  type Page,
  test as base,
} from "@playwright/test";

export { expect };

export const DANA = "dana.whitfield@undercover.example";
export const MARCUS = "marcus.oyelaran@undercover.example";
export const PRIYA = "priya.raman@undercover.example";
export const PASSWORD = "e2e-password";
const AUTH = "http://127.0.0.1:8004";

/** An access token from the stand-in for Supabase Auth. */
export async function tokenFor(
  request: APIRequestContext,
  email: string,
): Promise<string> {
  const response = await request.post(
    `${AUTH}/auth/v1/token?grant_type=password`,
    { data: { email, password: PASSWORD } },
  );
  const session = (await response.json()) as { access_token: string };
  return session.access_token;
}

/** Sign in on the sign-in page, first signing out whoever is signed in. */
export async function signInAs(page: Page, email: string): Promise<void> {
  await page.goto("/");
  const signOut = page.getByRole("button", { name: "Sign out" });
  const form = page.getByRole("form", { name: "Sign in" });
  await expect(signOut.or(form)).toBeVisible();
  if (await signOut.isVisible()) {
    await signOut.click();
    await expect(form).toBeVisible();
  }
  await form.getByLabel("Email address").fill(email);
  await form.getByLabel("Password").fill(PASSWORD);
  await form.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/** Every test starts signed in as Dana Whitfield, in the browser and the API. */
export const test = base.extend({
  page: async ({ page }, use) => {
    await signInAs(page, DANA);
    await use(page);
  },
  request: async ({ playwright, baseURL, request }, use) => {
    const token = await tokenFor(request, DANA);
    const signedIn = await playwright.request.newContext({
      baseURL,
      extraHTTPHeaders: { authorization: `Bearer ${token}` },
    });
    await use(signedIn);
    await signedIn.dispose();
  },
});
