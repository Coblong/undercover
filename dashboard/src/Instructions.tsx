import { useApi } from "./api";

export function Instructions() {
  const { data, failed } = useApi<{ text: string }>("/api/instructions");
  return (
    <section className="panel instructions" aria-label="Agent instructions">
      <h2>Agent instructions</h2>
      <p className="muted">
        What the UnderCover Agent is told before every claim. Read only.
      </p>
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
