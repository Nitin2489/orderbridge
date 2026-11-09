"use client";

import { ArrowRightIcon, LogOutIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

import { useLogout } from "../api";
import { logoutErrorMessage } from "../errors";
import { SIGN_IN_PATH, workspaceFor } from "../roles";
import { useSession } from "../session-provider";
import type { AuthenticatedUser } from "../types";

/** Header entry point: user identity, sign-out action, workspace navigation, or sign-in. */
export function SessionControls({ className }: { className?: string }) {
  const { status, user } = useSession();
  const pathname = usePathname();

  if (status === "authenticated" && user) {
    // Keyed by user: the header stays mounted across sign-out and sign-in
    // (it lives in the root layout), so each session gets a fresh sign-out
    // state instead of inheriting the previous session's.
    return <SignedInControls key={user.id} user={user} className={className} />;
  }

  if (status === "unauthenticated" && pathname !== SIGN_IN_PATH) {
    return (
      <Button asChild className={cn("h-11", className)}>
        <Link href={SIGN_IN_PATH}>Sign in</Link>
      </Button>
    );
  }

  return null;
}

function SignedInControls({
  user,
  className,
}: {
  user: AuthenticatedUser;
  className?: string;
}) {
  const logout = useLogout();
  const pathname = usePathname();

  const workspace = workspaceFor(user.role);
  const outsideWorkspace = Boolean(workspace && !pathname.startsWith(workspace.path));
  const displayName = user.retailer?.business_name ?? user.full_name;
  const roleLabel = user.role === "order_desk" ? "Order Desk" : "Retailer";
  // Only this sign-out request: page loads, navigation and other requests
  // never disable the button. On success the session is cleared while the
  // request is still pending, so this component unmounts before it ends.
  const busy = logout.isPending;

  return (
    <div className={cn("flex items-center gap-2 sm:gap-3", className)}>
      {outsideWorkspace && workspace ? (
        <Button asChild variant="outline" size="sm" className="h-9">
          <Link href={workspace.path}>
            <span className="sm:hidden">Workspace</span>
            <span className="hidden sm:inline">{workspace.label} workspace</span>
            <ArrowRightIcon className="size-3.5" aria-hidden="true" />
          </Link>
        </Button>
      ) : null}

      <div className="hidden sm:flex flex-col items-end text-right leading-tight">
        <span
          className="text-xs font-semibold text-foreground truncate max-w-[160px] md:max-w-[200px]"
          title={displayName}
        >
          {displayName}
        </span>
        <span className="text-[11px] font-medium text-primary">
          {roleLabel}
        </span>
      </div>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-11 min-w-11 px-2 text-muted-foreground hover:text-foreground gap-1.5"
        onClick={() => logout.mutate()}
        disabled={busy}
        title="Sign out"
        aria-label="Sign out"
      >
        {busy ? <Spinner /> : <LogOutIcon className="size-4" aria-hidden="true" />}
        <span className="hidden lg:inline text-xs font-medium">Sign out</span>
      </Button>

      {logout.isError ? (
        <p role="alert" className="max-w-40 text-xs font-medium text-destructive">
          {logoutErrorMessage(logout.error)}
        </p>
      ) : null}
    </div>
  );
}
