import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { dana, marcus, Refusal, settingsView, stubApi } from "./fixtures";
import { MeContext } from "./Me";
import { Settings } from "./Settings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

function page(me: typeof dana) {
  return render(
    <MeContext value={me}>
      <Settings />
    </MeContext>,
  );
}

it("shows every setting with its unit and who last changed it", async () => {
  stubApi({ "/api/settings": settingsView });
  page(dana);
  const form = await screen.findByRole("form", { name: "Settings" });
  expect(
    within(form).getByLabelText<HTMLInputElement>("Approval limit").value,
  ).toBe("1000");
  expect(
    within(form).getByLabelText<HTMLInputElement>("Floor on held-out accuracy")
      .value,
  ).toBe("60");
  expect(within(form).getByText("Dana Whitfield, Nov 3, 2026")).toBeDefined();
  expect(within(form).getAllByText("Starting value").length).toBeGreaterThan(0);
  const models = screen.getByRole("region", { name: "Models allowed" });
  expect(models.textContent).toContain("openai/gpt-6-luna");
  expect(models.textContent).toContain("$0.10");
});

it("is read only for a handler who can't sign off over the limit, and says why", async () => {
  stubApi({ "/api/settings": settingsView });
  page(marcus);
  const form = await screen.findByRole("form", { name: "Settings" });
  expect(within(form).getByRole("note").textContent).toContain(
    "Marcus Oyelaran can see the settings but not change them",
  );
  expect(
    within(form).getByLabelText<HTMLInputElement>("Approval limit").disabled,
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();
});

it("asks for confirmation, then saves the change", async () => {
  const fetchStub = stubApi({
    "/api/settings": settingsView,
    "PUT /api/settings": {
      ...settingsView,
      values: { ...settingsView.values, agents_at_once: 10 },
    },
  });
  page(dana);
  const agents = await screen.findByLabelText(
    "Claim agents at once in a round",
  );
  await userEvent.clear(agents);
  await userEvent.type(agents, "10");
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
  const dialog = await screen.findByRole("dialog", {
    name: "Save these settings?",
  });
  expect(dialog.textContent).toContain(
    "applies to the decisions recorded after it",
  );
  expect(fetchStub).not.toHaveBeenCalledWith(
    "/api/settings",
    expect.objectContaining({ method: "PUT" }),
  );
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Save settings" }),
  );
  await vi.waitFor(() => {
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  const put = fetchStub.mock.calls.find(([, init]) => init?.method === "PUT");
  const body = JSON.parse(
    put?.[1]?.body as string,
  ) as typeof settingsView.values;
  expect(body.agents_at_once).toBe(10);
  expect(body.objectives.accuracy).toBeCloseTo(0.6);
  expect(
    screen.getByLabelText<HTMLInputElement>("Claim agents at once in a round")
      .value,
  ).toBe("10");
});

it("does not save when the dialog is cancelled", async () => {
  const fetchStub = stubApi({ "/api/settings": settingsView });
  page(dana);
  await userEvent.click(
    await screen.findByRole("button", { name: "Save settings" }),
  );
  await userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Cancel",
    }),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(fetchStub.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
    false,
  );
});

it("asks for a number when a field is blank", async () => {
  stubApi({ "/api/settings": settingsView });
  page(dana);
  await userEvent.clear(
    await screen.findByLabelText("Spending limit for each round"),
  );
  await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Spending limit for each round needs a number.",
  );
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("gives the backend's reason when a change is refused", async () => {
  stubApi({
    "/api/settings": settingsView,
    "PUT /api/settings": new Refusal(403, {
      detail: "Marcus Oyelaran cannot change a setting",
    }),
  });
  page(dana);
  await userEvent.click(
    await screen.findByRole("button", { name: "Save settings" }),
  );
  await userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Save settings",
    }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not save the settings: Marcus Oyelaran cannot change a setting",
  );
});

it("switches the appearance and remembers it", async () => {
  stubApi({ "/api/settings": settingsView });
  page(dana);
  await userEvent.click(await screen.findByRole("radio", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("undercover-appearance")).toBe("dark");
  await userEvent.click(screen.getByRole("radio", { name: "Light" }));
  expect(document.documentElement.dataset.theme).toBe("light");
  await userEvent.click(
    screen.getByRole("radio", { name: "Same as this computer" }),
  );
  expect(document.documentElement.dataset.theme).toBeUndefined();
});

it("says what to do when the settings cannot load", async () => {
  stubApi({});
  page(dana);
  expect(screen.getByRole("status").textContent).toBe(
    "Loading the settings...",
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Reload the page",
  );
});
