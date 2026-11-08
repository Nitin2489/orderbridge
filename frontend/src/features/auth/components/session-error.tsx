"use client";

import { Button } from "@/components/ui/button";

import { sessionErrorMessage } from "../errors";
import { useSession } from "../session-provider";

/** The session could not be checked (network or server problem). */
export function SessionError() {
  const { error, refresh } = useSession();

  return (
    <section
      role="alert"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <h1 className="text-2xl font-semibold tracking-tight">Unable to continue</h1>
      <p className="text-muted-foreground">{sessionErrorMessage(error)}</p>
      <Button size="lg" onClick={refresh}>
        Try again
      </Button>
    </section>
  );
}
