import { useApi } from "./api";

export function Instructions() {
  const { data, failed } = useApi<{ text: string }>("/api/instructions/claim");
  return (
    <section className="panel instructions" aria-label="Agent instructions">
      <h2>Agent instructions</h2>
      <p className="muted">What the UnderCover Agent is told. Read only.</p>
      <h3>Working a claim</h3>
      {data ? (
        <pre>{data.text}</pre>
      ) : (
        <p role={failed ? "alert" : "status"}>
          {failed ? "Could not load the instructions." : "Loading..."}
        </p>
      )}
    </section>
  );
}
