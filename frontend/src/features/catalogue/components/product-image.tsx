import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A large, decorative product picture for the catalogue.
 *
 * [Assumption] No product photographs were supplied, so each category has
 * a simple illustration drawn inline (no network request on slow 3G). It
 * is decorative only: the product's name, size and SKU are always shown as
 * text, so the picture is hidden from screen readers.
 */

function Biscuits() {
  return (
    <>
      <rect x="22" y="30" width="76" height="40" rx="8" className="fill-primary/15 stroke-primary/40" strokeWidth="2" />
      {[38, 60, 82].map((cx) => (
        <g key={cx}>
          <circle cx={cx} cy="50" r="13" className="fill-amber-200 stroke-amber-500/70 dark:fill-amber-300/80" strokeWidth="2" />
          {[-5, 0, 5].map((dx) => (
            <circle key={dx} cx={cx + dx} cy={50 + (dx === 0 ? -4 : 3)} r="1.6" className="fill-amber-600/70" />
          ))}
        </g>
      ))}
    </>
  );
}

function Detergent() {
  return (
    <>
      <path d="M36 28 h48 l4 8 v38 a4 4 0 0 1 -4 4 h-48 a4 4 0 0 1 -4 -4 v-38 z" className="fill-primary/20 stroke-primary/50" strokeWidth="2" />
      <rect x="42" y="42" width="36" height="20" rx="4" className="fill-card stroke-primary/40" strokeWidth="1.5" />
      <path d="M52 52 q8 -8 16 0" className="fill-none stroke-primary" strokeWidth="2" strokeLinecap="round" />
      {[
        [92, 30, 5],
        [100, 44, 3.5],
        [26, 36, 3],
      ].map(([cx, cy, r]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={r} className="fill-none stroke-sky-400" strokeWidth="1.5" />
      ))}
    </>
  );
}

function Tea() {
  return (
    <>
      <path d="M38 40 h40 v18 a14 14 0 0 1 -14 14 h-12 a14 14 0 0 1 -14 -14 z" className="fill-primary/15 stroke-primary/50" strokeWidth="2" />
      <path d="M78 46 h4 a6 6 0 0 1 0 12 h-4" className="fill-none stroke-primary/50" strokeWidth="2" />
      {[48, 58, 68].map((x) => (
        <path key={x} d={`M${x} 34 q-3 -5 0 -10`} className="fill-none stroke-muted-foreground/50" strokeWidth="1.5" strokeLinecap="round" />
      ))}
      <path d="M86 66 q8 -12 18 -10 q-4 12 -18 10 z" className="fill-emerald-500/70" />
    </>
  );
}

function Package() {
  return (
    <>
      <path d="M60 22 l30 14 v28 l-30 14 l-30 -14 v-28 z" className="fill-primary/15 stroke-primary/50" strokeWidth="2" strokeLinejoin="round" />
      <path d="M30 36 l30 14 l30 -14 M60 50 v28" className="fill-none stroke-primary/50" strokeWidth="2" strokeLinejoin="round" />
    </>
  );
}

const ILLUSTRATIONS: Record<string, () => ReactNode> = {
  biscuits: Biscuits,
  "home care": Detergent,
  beverages: Tea,
};

export function ProductImage({
  category,
  unavailable = false,
  className,
}: {
  category: string;
  unavailable?: boolean;
  className?: string;
}) {
  // Plain functions without hooks, so they are called rather than rendered
  // as components chosen at render time.
  const illustration = (ILLUSTRATIONS[category.trim().toLowerCase()] ?? Package)();
  return (
    <div
      aria-hidden="true"
      data-testid="product-image"
      className={cn(
        "flex aspect-[16/9] items-center justify-center bg-secondary/60",
        unavailable && "opacity-50 grayscale",
        className,
      )}
    >
      <svg viewBox="0 0 120 90" className="h-full max-h-36 w-auto" focusable="false">
        {illustration}
      </svg>
    </div>
  );
}
