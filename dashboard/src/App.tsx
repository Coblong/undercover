import { useState } from "react";
import { useApi } from "./api";
import { ClaimView } from "./ClaimView";
import { Queue } from "./Queue";
import type { ClaimSummary } from "./types";

export function App() {
  const { data: claims, failed } = useApi<ClaimSummary[]>("/api/claims");
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <>
      <header className="top">
        <h1>UnderCover</h1>
        <p className="tagline">Claims, worked straight through</p>
      </header>
      <main className="layout">
        {claims ? (
          <Queue claims={claims} selected={selected} onSelect={setSelected} />
        ) : (
          <p className="panel" role={failed ? "alert" : "status"}>
            {failed ? "Could not load the claims." : "Loading claims..."}
          </p>
        )}
        {selected ? (
          <ClaimView key={selected} claimId={selected} />
        ) : (
          <p className="panel muted">Select a claim to see it in full.</p>
        )}
      </main>
    </>
  );
}
