"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";

import { isApiError } from "@/lib/api";

import { AUTH_KEY_PREFIX, endClientSession, SESSION_QUERY_KEY, useSessionQuery } from "./api";
import type { AuthenticatedUser } from "./types";

export type SessionStatus = "loading" | "authenticated" | "unauthenticated" | "error";

export interface SessionState {
  status: SessionStatus;
  /** Set only when status is "authenticated". */
  user: AuthenticatedUser | null;
  /** Set only when status is "error": the session could not be checked. */
  error: unknown;
  /** Re-check the session with the backend. */
  refresh: () => void;
}

const SessionContext = createContext<SessionState | null>(null);

function isUnauthorized(error: unknown): boolean {
  return isApiError(error) && error.status === 401;
}

/**
 * The signed-in user, recovered from the backend's /auth/me on every page
 * load (the HttpOnly cookie is the only credential, so there is nothing to
 * read from browser storage).
 *
 * It also ends the client session when any other request comes back 401,
 * which is how an expired or revoked session is noticed between checks.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, error, refetch } = useSessionQuery();

  useEffect(() => {
    const expireIfSignedIn = (failure: unknown) => {
      if (isUnauthorized(failure) && queryClient.getQueryData(SESSION_QUERY_KEY)) {
        void endClientSession(queryClient);
      }
    };

    const unsubscribeQueries = queryClient.getQueryCache().subscribe((event) => {
      if (
        event.type === "updated" &&
        event.action.type === "error" &&
        event.query.queryKey[0] !== AUTH_KEY_PREFIX
      ) {
        expireIfSignedIn(event.action.error);
      }
    });

    const unsubscribeMutations = queryClient.getMutationCache().subscribe((event) => {
      // Auth mutations are excluded: a 401 from verify-otp means a wrong
      // code, not an expired session.
      if (
        event.type === "updated" &&
        event.action.type === "error" &&
        event.mutation.options.mutationKey?.[0] !== AUTH_KEY_PREFIX
      ) {
        expireIfSignedIn(event.action.error);
      }
    });

    return () => {
      unsubscribeQueries();
      unsubscribeMutations();
    };
  }, [queryClient]);

  const value = useMemo<SessionState>(() => {
    const refresh = () => void refetch();

    // Cached data wins over a failed background re-check, so a brief network
    // problem does not sign anyone out. A real 401 resolves to null instead.
    if (data !== undefined) {
      return data === null
        ? { status: "unauthenticated", user: null, error: null, refresh }
        : { status: "authenticated", user: data, error: null, refresh };
    }
    if (error) {
      return { status: "error", user: null, error, refresh };
    }
    return { status: "loading", user: null, error: null, refresh };
  }, [data, error, refetch]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const session = useContext(SessionContext);
  if (session === null) {
    throw new Error("useSession must be used inside SessionProvider.");
  }
  return session;
}
