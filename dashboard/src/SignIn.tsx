import { useState } from "react";
import { signIn } from "./supabase";

export function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  return (
    <main className="sign-in">
      <form
        className="panel"
        aria-label="Sign in"
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          setFailure(null);
          signIn(email, password)
            .catch((error: unknown) => {
              setFailure(
                error instanceof Error ? error.message : "Could not sign in",
              );
            })
            .finally(() => {
              setBusy(false);
            });
        }}
      >
        <h1>UnderCover</h1>
        <p className="tagline">Claims, worked straight through</p>
        <h2>Sign in</h2>
        <p className="muted">Use your UnderCover email address and password.</p>
        <label>
          Email address
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          Sign in
        </button>
        {failure && <p role="alert">{failure}</p>}
      </form>
    </main>
  );
}
