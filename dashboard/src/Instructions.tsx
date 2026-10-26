import { useApi } from "./api";

function Text({ agent }: Readonly<{ agent: "queue" | "claim" }>) {
  const { data, failed } = useApi<{ text: string }>(
    `/api/instructions/${agent}`,
  );
  if (data) return <pre>{data.text}</pre>;
  return (
    <p role={failed ? "alert" : "status"}>
      {failed ? "Could not load the instructions." : "Loading..."}
    </p>
  );
}

export function Instructions() {
  return (
    <section className="panel instructions" aria-label="Agent instructions">
      <h2>Agent instructions</h2>
      <p className="muted">What the UnderCover Agent is told. Read only.</p>
      <h3>Running the queue</h3>
      <Text agent="queue" />
      <h3>Working a claim</h3>
      <Text agent="claim" />
    </section>
  );
}
