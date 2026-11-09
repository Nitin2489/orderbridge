import type * as React from "react";

import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

/** A centred, announced loading message for a page or panel. */
function LoadingState({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  /** Optional extra text under the label, announced with it. */
  children?: React.ReactNode;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex min-h-48 flex-col items-center justify-center gap-3 text-sm text-muted-foreground",
        className,
      )}
    >
      <Spinner className="size-6 text-primary" />
      <span>{label}</span>
      {children}
    </div>
  );
}

export { LoadingState };
