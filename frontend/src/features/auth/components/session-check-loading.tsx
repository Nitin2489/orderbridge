"use client";

import { useEffect, useState } from "react";

import { LoadingState } from "@/components/ui/loading-state";

/** How long the session check may take before the wake-up note appears. */
export const WAKE_UP_HINT_DELAY_MS = 3_000;

export const WAKE_UP_HINT =
  "The server may be waking up after a quiet period. This can take up to about a minute.";

/**
 * The session-check spinner. If the check is still running after a few
 * seconds, it explains that the hosted backend may be starting up (it sleeps
 * when idle). This is only an explanation: nothing is retried or cancelled,
 * and the note disappears with the spinner when the check completes.
 */
export function SessionCheckLoading() {
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), WAKE_UP_HINT_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <LoadingState label="Checking your session…">
      {slow ? <p className="max-w-sm text-center text-sm">{WAKE_UP_HINT}</p> : null}
    </LoadingState>
  );
}
