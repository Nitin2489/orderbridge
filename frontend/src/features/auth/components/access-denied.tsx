import { ShieldAlertIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

import type { Workspace } from "../roles";
import { SignOutButton } from "./sign-out-button";

/**
 * Shown when a signed-in user opens a workspace that is not theirs, or has
 * a role this app does not support. The backend would refuse the data
 * anyway (403); this keeps the UI honest about it.
 */
export function AccessDenied({ ownWorkspace }: { ownWorkspace: Workspace | null }) {
  return (
    <section
      aria-labelledby="access-denied-heading"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <ShieldAlertIcon className="size-6" aria-hidden="true" />
      </span>
      <h1 id="access-denied-heading" className="text-2xl font-semibold tracking-tight">
        You do not have access to this page
      </h1>
      <p className="text-muted-foreground">
        {ownWorkspace
          ? "This area belongs to a different kind of account."
          : "Your account cannot use OrderBridge on the web yet. Contact the order desk for help."}
      </p>
      <div className="flex flex-col items-center gap-3 sm:flex-row sm:items-start">
        {ownWorkspace ? (
          <Button asChild size="lg">
            <Link href={ownWorkspace.path}>Go to {ownWorkspace.label.toLowerCase()} workspace</Link>
          </Button>
        ) : null}
        <SignOutButton className="items-center" />
      </div>
    </section>
  );
}
