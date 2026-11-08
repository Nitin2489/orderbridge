import { Loader2Icon } from "lucide-react";

import { cn } from "@/lib/utils";

/** Decorative spinner. Pair it with visible or screen-reader text. */
function Spinner({ className }: { className?: string }) {
  return (
    <Loader2Icon
      aria-hidden="true"
      className={cn("size-4 shrink-0 animate-spin motion-reduce:animate-none", className)}
    />
  );
}

export { Spinner };
