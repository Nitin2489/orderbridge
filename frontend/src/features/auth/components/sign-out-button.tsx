"use client";

import { LogOutIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

import { useLogout } from "../api";
import { logoutErrorMessage } from "../errors";

/**
 * Ends the session on the backend, then clears cached data and goes to
 * sign-in. If the backend call fails, nothing is cleared and the error is
 * shown: pretending to be signed out while the cookie is still valid would
 * be misleading on a shared phone.
 */
export function SignOutButton({ className }: { className?: string }) {
  const logout = useLogout();
  const busy = logout.isPending || logout.isSuccess;

  return (
    <div className={cn("flex flex-col items-start gap-2", className)}>
      <Button
        type="button"
        variant="outline"
        className="h-11"
        onClick={() => logout.mutate()}
        disabled={busy}
      >
        {busy ? <Spinner /> : <LogOutIcon aria-hidden="true" />}
        {busy ? "Signing out…" : "Sign out"}
      </Button>
      {logout.isError ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {logoutErrorMessage(logout.error)}
        </p>
      ) : null}
    </div>
  );
}
