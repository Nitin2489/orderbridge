import Link from "next/link";

import { cn } from "@/lib/utils";

/** The OrderBridge mark: two order lines joined by a bridge. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn("size-8", className)}
      aria-hidden="true"
      focusable="false"
    >
      <rect width="32" height="32" rx="8" className="fill-primary" />
      <path
        d="M8 11h16M8 21h16M12 11v10M20 11v10"
        className="stroke-primary-foreground"
        strokeWidth="2.25"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

export function Brand({ className }: { className?: string }) {
  return (
    <Link
      href="/"
      className={cn(
        "inline-flex min-h-11 items-center gap-2.5 rounded-md text-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        className,
      )}
    >
      <BrandMark />
      <span className="text-lg font-semibold tracking-tight">OrderBridge</span>
    </Link>
  );
}
