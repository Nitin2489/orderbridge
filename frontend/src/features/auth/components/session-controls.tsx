"use client";

import { ArrowRightIcon, LogOutIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

import { useLogout } from "../api";
import { SIGN_IN_PATH, workspaceFor } from "../roles";
import { useSession } from "../session-provider";

/** Header entry point: user identity, sign-out action, workspace navigation, or sign-in. */
export function SessionControls({ className }: { className?: string }) {
  const { status, user } = useSession();
  const logout = useLogout();
  const pathname = usePathname();

  if (status === "authenticated" && user) {
    const workspace = workspaceFor(user.role);
    const outsideWorkspace = Boolean(workspace && !pathname.startsWith(workspace.path));
    const displayName = user.retailer?.business_name ?? user.full_name;
    const roleLabel = user.role === "order_desk" ? "Order Desk" : "Retailer";
    const busy = logout.isPending || logout.isSuccess;

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
          className="h-9 px-2 text-muted-foreground hover:text-foreground gap-1.5"
          onClick={() => logout.mutate()}
          disabled={busy}
          title="Sign out"
          aria-label="Sign out"
        >
          {busy ? <Spinner /> : <LogOutIcon className="size-4" aria-hidden="true" />}
          <span className="hidden lg:inline text-xs font-medium">Sign out</span>
        </Button>
      </div>
    );
  }

  if (status === "unauthenticated" && pathname !== SIGN_IN_PATH) {
    return (
      <Button asChild className={cn("h-10", className)}>
        <Link href={SIGN_IN_PATH}>Sign in</Link>
      </Button>
    );
  }

  return null;
}
