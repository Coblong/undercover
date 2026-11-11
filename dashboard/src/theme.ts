// The appearance is the one setting each browser remembers for itself.
export type Appearance = "system" | "light" | "dark";

const KEY = "undercover-appearance";

export const APPEARANCES: Record<Appearance, string> = {
  system: "Same as this computer",
  light: "Light",
  dark: "Dark",
};

function isAppearance(value: unknown): value is Appearance {
  return value === "system" || value === "light" || value === "dark";
}

export function getAppearance(): Appearance {
  try {
    const stored = localStorage.getItem(KEY);
    return isAppearance(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

/** Show the appearance now, and keep it for next time where the browser allows. */
export function setAppearance(appearance: Appearance): void {
  if (appearance === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = appearance;
  try {
    localStorage.setItem(KEY, appearance);
  } catch {
    // The choice holds for this visit even when the browser won't keep it.
  }
}
