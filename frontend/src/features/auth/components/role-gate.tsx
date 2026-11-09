"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { LoadingState } from "@/components/ui/loading-state";

import { SIGN_IN_PATH, workspaceFor, type UserRole } from "../roles";
import { useSession } from "../session-provider";
import { AccessDenied } from "./access-denied";
import { SessionCheckLoading } from "./session-check-loading";
import { SessionError } from "./session-error";

/**
 * Renders a workspace only for a signed-in user with `role`.
 *
 * This is a user-experience guard, not security: the backend authorises
 * every request from the session cookie and returns 401/403 regardless of
 * what the browser renders.
 */
export function RoleGate({ role, children }: { role: UserRole; children: ReactNode }) {
  const session = useSession();
  const router = useRouter();
  const signedOut = session.status === "unauthenticated";

  useEffect(() => {
    if (signedOut) {
      router.replace(SIGN_IN_PATH);
    }
  }, [signedOut, router]);

  switch (session.status) {
    case "loading":
      return <SessionCheckLoading />;
    case "error":
      return <SessionError />;
    case "unauthenticated":
      return <LoadingState label="Redirecting to sign in…" />;
    case "authenticated":
      if (session.user?.role === role) {
        return <>{children}</>;
      }
      return <AccessDenied ownWorkspace={session.user ? workspaceFor(session.user.role) : null} />;
  }
}
