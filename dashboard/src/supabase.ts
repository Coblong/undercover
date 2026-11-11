// The dashboard's one Supabase client. Handlers sign in with an email address and a
// password, and the backend checks the access token this gives.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let built: SupabaseClient | null | undefined;

function client(): SupabaseClient | null {
  if (built === undefined) {
    const url: unknown = import.meta.env.VITE_SUPABASE_URL;
    const key: unknown = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
    built =
      typeof url === "string" && url && typeof key === "string" && key
        ? createClient(url, key)
        : null;
  }
  return built;
}

export async function signIn(email: string, password: string): Promise<void> {
  const supabase = client();
  if (!supabase) {
    throw new Error(
      "Sign-in is not set up. Put VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY in .env",
    );
  }
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw new Error("That email address and password do not match");
}

export async function signOut(): Promise<void> {
  await client()?.auth.signOut();
}

/** The access token to send with a request, or null when nobody is signed in. */
export async function accessToken(): Promise<string | null> {
  const supabase = client();
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

/** Tell `onChange` whether a handler is signed in now and whenever that changes. */
export function watchSignedIn(
  onChange: (signedIn: boolean) => void,
): () => void {
  const supabase = client();
  if (!supabase) {
    onChange(false);
    return () => undefined;
  }
  void supabase.auth.getSession().then(({ data }) => {
    onChange(data.session !== null);
  });
  const { data } = supabase.auth.onAuthStateChange((_event, session) => {
    onChange(session !== null);
  });
  return () => {
    data.subscription.unsubscribe();
  };
}
