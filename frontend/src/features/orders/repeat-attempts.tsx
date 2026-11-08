"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { useSession } from "@/features/auth/session-provider";
import type { AuthenticatedUser } from "@/features/auth/types";

/**
 * Idempotency keys of repeat attempts whose outcome is not final, one per
 * source order, for the signed-in retailer only.
 *
 * - keyFor(source) returns the pending key for that source order, or creates
 *   and remembers a new one: retrying an uncertain failure reuses the key.
 * - finish(source, key) forgets that key once its outcome is final, so the
 *   next explicit repeat gets a new key. Only the exact key is removed: a
 *   late response for an earlier attempt (or an earlier session) cannot
 *   remove a newer key.
 * - Keys belong to the retailer they were created for: any change of the
 *   session's retailer (switching, signing out, signing in again) discards
 *   them. Changes are taken from the query cache as they happen, not from
 *   rendering, so even a change the UI never got to render is not missed.
 *
 * Kept in memory only (a ref read from event handlers, never during render),
 * and above the order screens so a retry after navigating away and back
 * still uses the same key.
 */

interface RepeatAttempts {
  /** Null when no retailer is signed in: nothing can be repeated. */
  keyFor: (sourceOrderId: string) => string | null;
  /** Forget `clientRequestId` if it is still the pending key for the source order. */
  finish: (sourceOrderId: string, clientRequestId: string) => void;
}

const RepeatAttemptsContext = createContext<RepeatAttempts | null>(null);

function newClientRequestId(): string {
  return globalThis.crypto.randomUUID();
}

/** The retailer a session belongs to, or null (signed out, or not a retailer). */
function retailerOf(user: AuthenticatedUser | null | undefined): string | null {
  return user?.role === "retailer" ? (user.retailer?.id ?? null) : null;
}

export function RepeatAttemptsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { user } = useSession();
  const ownerId = retailerOf(user);
  const store = useRef<{ ownerId: string | null; keys: Map<string, string> }>({
    ownerId: null,
    keys: new Map(),
  });

  // Follow the session in the query cache. Cache listeners run synchronously
  // with every session change (rendering is batched and can skip states), so
  // every switch of retailer, sign-out or sign-in discards pending keys, even
  // A -> B -> A within one tick.
  useEffect(() => {
    return queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== "updated" && event.type !== "removed") {
        return;
      }
      const [scope, name] = event.query.queryKey;
      if (scope !== SESSION_QUERY_KEY[0] || name !== SESSION_QUERY_KEY[1]) {
        return;
      }
      const next =
        event.type === "removed"
          ? null
          : retailerOf(event.query.state.data as AuthenticatedUser | null | undefined);
      if (next !== store.current.ownerId) {
        store.current = { ownerId: next, keys: new Map() };
      }
    });
  }, [queryClient]);

  /** The current retailer's keys, discarding any left by someone else. */
  const keysForOwner = (): Map<string, string> | null => {
    if (store.current.ownerId !== ownerId) {
      store.current = { ownerId, keys: new Map() };
    }
    return ownerId ? store.current.keys : null;
  };

  const value: RepeatAttempts = {
    keyFor: (sourceOrderId) => {
      const keys = keysForOwner();
      if (!keys) {
        return null;
      }
      let key = keys.get(sourceOrderId);
      if (!key) {
        key = newClientRequestId();
        keys.set(sourceOrderId, key);
      }
      return key;
    },
    finish: (sourceOrderId, clientRequestId) => {
      // Keys are unique: a late response for an earlier attempt, retailer or
      // session finds a different (or no) key here and changes nothing.
      const keys = store.current.keys;
      if (keys.get(sourceOrderId) === clientRequestId) {
        keys.delete(sourceOrderId);
      }
    },
  };

  return (
    <RepeatAttemptsContext.Provider value={value}>{children}</RepeatAttemptsContext.Provider>
  );
}

export function useRepeatAttempts(): RepeatAttempts {
  const attempts = useContext(RepeatAttemptsContext);
  if (attempts === null) {
    throw new Error("useRepeatAttempts must be used inside RepeatAttemptsProvider.");
  }
  return attempts;
}
