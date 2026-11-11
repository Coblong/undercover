import { createContext, useContext } from "react";
import type { Handler } from "./types";

/** The signed-in handler, which the pages use to say what they can't do. */
export const MeContext = createContext<Handler | null>(null);

export function useMe(): Handler | null {
  return useContext(MeContext);
}

export const CANNOT_APPROVE =
  "Only a handler who can sign off payouts over the limit can do this.";
