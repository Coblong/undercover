import type { DiffLine } from "./types";

const MARK: Record<DiffLine["kind"], string> = {
  add: "+",
  remove: "-",
  context: " ",
};

const LABEL: Record<DiffLine["kind"], string> = {
  add: "Added",
  remove: "Removed",
  context: "Unchanged",
};

/** What changed between two texts: additions and removals in color, and marked. */
export function Diff({ lines }: Readonly<{ lines: DiffLine[] }>) {
  if (lines.length === 0) {
    return <p className="muted">Nothing changed in the text.</p>;
  }
  return (
    <div className="diff" role="group" aria-label="Changes">
      {lines.map((line, position) => (
        <div
          key={`${String(position)}-${line.kind}`}
          className={`diff-line diff-${line.kind}`}
          title={LABEL[line.kind]}
        >
          <span className="diff-mark" aria-hidden="true">
            {MARK[line.kind]}
          </span>
          <span className="diff-text">{line.text || " "}</span>
          <span className="visually-hidden">{LABEL[line.kind]}</span>
        </div>
      ))}
    </div>
  );
}
