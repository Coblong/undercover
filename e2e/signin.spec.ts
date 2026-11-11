import { expect, signInAs, tokenFor, test, PASSWORD, DANA } from "./fixtures";

test("every page needs a sign-in, and a wrong password is refused", async ({
  page,
  request,
}) => {
  await page.getByRole("button", { name: "Sign out" }).click();
  const form = page.getByRole("form", { name: "Sign in" });
  await expect(form).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);

  await form.getByLabel("Email address").fill(DANA);
  await form.getByLabel("Password").fill("not the password");
  await form.getByRole("button", { name: "Sign in" }).click();
  await expect(form.getByRole("alert")).toHaveText(
    "That email address and password do not match",
  );

  await form.getByLabel("Password").fill(PASSWORD);
  await form.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Signed in as")).toContainText("Dana Whitfield");

  const anonymous = await request.get("/api/me", {
    headers: { authorization: "" },
  });
  expect(anonymous.status()).toBe(401);
  const token = await tokenFor(request, DANA);
  expect(token).toBeTruthy();
});

test("the header shows who is signed in and a sign-in lasts a reload", async ({
  page,
}) => {
  await expect(page.getByText("Signed in as")).toContainText("Dana Whitfield");
  await page.reload();
  await expect(page.getByText("Signed in as")).toContainText("Dana Whitfield");
  await signInAs(page, "marcus.oyelaran@undercover.example");
  await expect(page.getByText("Signed in as")).toContainText("Marcus Oyelaran");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("form", { name: "Sign in" })).toBeVisible();
});
