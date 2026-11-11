import { expect, MARCUS, signInAs, test } from "./fixtures";

test("the configuration shows version 1 and cannot be edited on the page", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Configuration" }).click();
  await expect(
    page.getByRole("heading", { name: "Configuration", level: 2 }),
  ).toBeVisible();
  const current = page.getByRole("region", { name: "Current version" });
  await expect(current).toContainText("openai/gpt-6-luna");
  await expect(current).toContainText("Version 1");
  const version = page.getByRole("region", { name: "Version 1" });
  await expect(version).toContainText("This is the first version.");
  await version.getByText("Instructions in full").click();
  await expect(version).toContainText("get_document_section");
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

test("a senior handler changes a setting and it is there after a reload", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Settings" }).click();
  const agents = page.getByLabel("Claim agents at once in a round");
  await expect(agents).toHaveValue("25");
  await agents.fill("12");
  await page.getByRole("button", { name: "Save settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Save these settings?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Save settings" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Dana Whitfield, ").first()).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByLabel("Claim agents at once in a round")).toHaveValue(
    "12",
  );
  await page.getByLabel("Claim agents at once in a round").fill("25");
  await page.getByRole("button", { name: "Save settings" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Save settings" })
    .click();
});

test("a handler who can't sign off over the limit sees the settings read only", async ({
  page,
}) => {
  await signInAs(page, MARCUS);
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("note")).toContainText(
    "Marcus Oyelaran can see the settings but not change them",
  );
  await expect(page.getByLabel("Approval limit")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Save settings" })).toHaveCount(
    0,
  );
});

test("the appearance can be switched and each browser remembers it", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("radio", { name: "Dark" }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("radio", { name: "Dark" })).toBeChecked();
  await page.getByRole("radio", { name: "Same as this computer" }).check();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
});
