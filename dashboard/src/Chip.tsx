import type { Tone } from "./format";

export function Chip({ tone, label }: Readonly<{ tone: Tone; label: string }>) {
  return <span className={`chip tone-${tone}`}>{label}</span>;
}
