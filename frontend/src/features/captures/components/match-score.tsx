import { cn } from "@/lib/utils";

import type { Candidate } from "../types";

const BAND: Record<Candidate["band"], { label: string; bar: string; text: string }> = {
  strong: { label: "Strong match", bar: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-300" },
  possible: { label: "Possible match", bar: "bg-sky-500", text: "text-sky-700 dark:text-sky-300" },
  weak: { label: "Weak match", bar: "bg-amber-500", text: "text-amber-700 dark:text-amber-300" },
  very_weak: { label: "Very weak match", bar: "bg-muted-foreground/40", text: "text-muted-foreground" },
};

/**
 * A heuristic ranking score, worded as such: "60/100 · Possible match".
 * Never a percentage or a confidence: it is a fixed rule-based score.
 */
export function MatchScore({ candidate, className }: { candidate: Candidate; className?: string }) {
  const band = BAND[candidate.band];
  return (
    <span className={cn("inline-flex items-center gap-2 text-xs", className)}>
      <span aria-hidden="true" className="h-1.5 w-12 overflow-hidden rounded-full bg-secondary">
        <span
          className={cn("block h-full rounded-full", band.bar)}
          style={{ width: `${candidate.match_score}%` }}
        />
      </span>
      <span className={cn("font-medium tabular-nums", band.text)}>
        <span className="sr-only">Match score </span>
        {candidate.match_score}/100 · {band.label}
      </span>
    </span>
  );
}
