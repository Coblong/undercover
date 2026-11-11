import { useEffect, useState } from "react";
import { always, useApi } from "./api";
import { ClaimView } from "./ClaimView";
import { Configuration } from "./Configuration";
import { DispatchPanel } from "./DispatchPanel";
import { Improvement } from "./Improvement";
import { MeContext } from "./Me";
import { PageHeader } from "./PageHeader";
import { Queue } from "./Queue";
import { Scoreboard } from "./Scoreboard";
import { Settings } from "./Settings";
import { SignIn } from "./SignIn";
import { signOut, watchSignedIn } from "./supabase";
import type { ClaimSummary, Handler } from "./types";

type View =
  "claims" | "scoreboard" | "improvement" | "configuration" | "settings";

const VIEWS: Record<View, string> = {
  claims: "Claims",
  scoreboard: "Scoreboard",
  improvement: "Improvement",
  configuration: "Configuration",
  settings: "Settings",
};

function Dashboard({ me }: Readonly<{ me: Handler }>) {
  const {
    data: claims,
    failed,
    reload,
  } = useApi<ClaimSummary[]>("/api/claims", always);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View>("claims");

  return (
    <MeContext value={me}>
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
        <div className="who">
          <span>
            Signed in as <strong>{me.name}</strong>
          </span>
          <button
            type="button"
            className="link"
            onClick={() => {
              void signOut();
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      {view === "improvement" && <Improvement />}
      {view === "configuration" && <Configuration />}
      {view === "settings" && <Settings />}
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
        <main className="page">
          <PageHeader
            title="Claims"
            purpose="The queue, the current dispatch and each claim in full."
          />
          <div className="layout">
            <DispatchPanel />
            {claims ? (
              <Queue
                claims={claims}
                selected={selected}
                onSelect={setSelected}
              />
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
          </div>
        </main>
      )}
    </MeContext>
  );
}

/** Loads the signed-in handler, or says why this sign-in can't use the dashboard. */
function Handlers() {
  const { data: me, failed } = useApi<Handler>("/api/me");
  if (me) return <Dashboard me={me} />;
  return (
    <main className="sign-in">
      <div className="panel" role={failed ? "alert" : "status"}>
        {failed ? (
          <>
            <p>
              This sign-in is not one of UnderCover&apos;s claims handlers, so
              it can&apos;t use the dashboard.
            </p>
            <button
              type="button"
              className="primary"
              onClick={() => {
                void signOut();
              }}
            >
              Sign out
            </button>
          </>
        ) : (
          "Loading..."
        )}
      </div>
    </main>
  );
}

export function App() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  useEffect(() => watchSignedIn(setSignedIn), []);

  if (signedIn === null) {
    return (
      <p className="panel" role="status">
        Loading...
      </p>
    );
  }
  return signedIn ? <Handlers /> : <SignIn />;
}
