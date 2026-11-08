"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";

/** Route-level error boundary: a calm message and a working retry. */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log for developers; the user only sees the generic message below.
    console.error(error);
  }, [error]);

  return (
    <section
      role="alert"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <h1 className="text-2xl font-semibold tracking-tight">Something went wrong</h1>
      <p className="text-muted-foreground">
        This page could not be displayed. Please try again.
      </p>
      <Button size="lg" onClick={reset}>
        Try again
      </Button>
    </section>
  );
}
