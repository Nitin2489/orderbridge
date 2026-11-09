"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { LoadingState } from "@/components/ui/loading-state";

import { workspaceFor } from "../roles";
import { useSession } from "../session-provider";
import { AccessDenied } from "./access-denied";
import { SessionCheckLoading } from "./session-check-loading";
import { SignInFlow } from "./sign-in-flow";

/**
 * /sign-in. Signed-in users are sent to their workspace, which is also how
 * a successful verification lands: verify-otp puts the user in the session
 * cache, and this effect redirects.
 */
export function SignInScreen() {
  const session = useSession();
  const router = useRouter();
  const workspace = session.user ? workspaceFor(session.user.role) : null;

  useEffect(() => {
    if (workspace) {
      router.replace(workspace.path);
    }
  }, [workspace, router]);

  if (session.status === "loading") {
    return <SessionCheckLoading />;
  }
  if (session.status === "authenticated") {
    return workspace ? (
      <LoadingState label="Opening your workspace…" />
    ) : (
      <AccessDenied ownWorkspace={null} />
    );
  }
  // Unauthenticated, or the session check failed: signing in is still the
  // right next step, and its own requests report any connection problem.
  return <SignInFlow />;
}
