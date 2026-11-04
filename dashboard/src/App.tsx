import { useState } from "react";
import { always, useApi } from "./api";
import { ClaimView } from "./ClaimView";
import { DispatchPanel } from "./DispatchPanel";
import { Instructions } from "./Instructions";
import { Queue } from "./Queue";
import { Scoreboard } from "./Scoreboard";
import type { ClaimSummary } from "./types";

type View = "claims" | "scoreboard" | "instructions";

const VIEWS: Record<View, string> = {
  claims: "Claims",
  scoreboard: "Scoreboard",
  instructions: "Instructions",
};

export function App() {
  const {
    data: claims,
    failed,
    reload,
  } = useApi<ClaimSummary[]>("/api/claims", always);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View>("claims");

  return (
    <>
      <header className="top">
        <h1>UnderCover</h1>
        <p className="tagline">Claims, worked straight through</p>
        <nav aria-label="Views">
          {(Object.keys(VIEWS) as View[]).map((name) => (
            <button
              key={name}
              type="button"
              aria-current={view === name ? "page" : undefined}
              onClick={() => {
                setView(name);
              }}
            >
              {VIEWS[name]}
            </button>
          ))}
        </nav>
      </header>
      {view === "instructions" && (
        <main className="single">
          <Instructions />
        </main>
      )}
      {view === "scoreboard" && (
        <main className="single wide">
          <Scoreboard
            onOpenClaim={(claimId) => {
              setSelected(claimId);
              setView("claims");
            }}
          />
        </main>
      )}
      {view === "claims" && (
        <main className="layout">
          <DispatchPanel />
          {claims ? (
            <Queue claims={claims} selected={selected} onSelect={setSelected} />
          ) : (
            <p className="panel" role={failed ? "alert" : "status"}>
              {failed ? "Could not load the claims." : "Loading claims..."}
            </p>
          )}
          {selected ? (
            <ClaimView key={selected} claimId={selected} onChange={reload} />
          ) : (
            <p className="panel muted">Select a claim to see it in full.</p>
          )}
        </main>
      )}
    </>
  );
}
