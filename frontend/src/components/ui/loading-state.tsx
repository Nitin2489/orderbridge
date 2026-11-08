import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

/** A centred, announced loading message for a page or panel. */
function LoadingState({ label, className }: { label: string; className?: string }) {
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
    </div>
  );
}

export { LoadingState };
