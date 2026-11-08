import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A row of labelled figures (quantities, prices, credit): label-value rows
 * on phones, side-by-side columns from the small breakpoint up, so a desk
 * screen scans across instead of down a narrow list.
 */
export function Figures({
  children,
  columns = 4,
  className,
}: {
  children: ReactNode;
  /** Columns on large screens. */
  columns?: 3 | 4 | 5 | 6 | 7;
  className?: string;
}) {
  const large = {
    3: "lg:grid-cols-3",
    4: "lg:grid-cols-4",
    5: "lg:grid-cols-5",
    6: "lg:grid-cols-6",
    7: "lg:grid-cols-7",
  }[columns];
  return (
    <dl
      className={cn(
        "grid gap-y-1 text-sm sm:grid-cols-3 sm:gap-x-6 sm:gap-y-3",
        large,
        className,
      )}
    >
      {children}
    </dl>
  );
}

export function Figure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 sm:flex-col sm:items-start sm:justify-start sm:gap-0.5">
      <dt className="text-muted-foreground sm:text-xs">{label}</dt>
      <dd className="text-right tabular-nums sm:text-left sm:font-medium">{children}</dd>
    </div>
  );
}
