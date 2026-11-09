import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

export interface BreadcrumbItem {
  label: string;
  href?: string;
  current?: boolean;
}

export interface BreadcrumbsProps {
  items: BreadcrumbItem[];
  className?: string;
}

/**
 * WAI-ARIA compliant breadcrumb navigation.
 * Visual separators are hidden from screen readers; the current leaf item has aria-current="page".
 */
export function Breadcrumbs({ items, className }: BreadcrumbsProps) {
  if (items.length <= 1) {
    return null;
  }

  return (
    <nav aria-label="Breadcrumb" className={cn("text-sm text-muted-foreground", className)}>
      <ol className="flex flex-wrap items-center gap-1.5 sm:gap-2">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;
          const isCurrent = item.current ?? isLast;

          return (
            <li key={index} className="inline-flex items-center gap-1.5 sm:gap-2">
              {index > 0 ? (
                <ChevronRightIcon
                  className="size-3.5 shrink-0 text-muted-foreground/60"
                  aria-hidden="true"
                />
              ) : null}
              {isCurrent || !item.href ? (
                <span
                  aria-current={isCurrent ? "page" : undefined}
                  className="font-medium text-foreground truncate max-w-[200px] sm:max-w-none"
                >
                  {item.label}
                </span>
              ) : (
                <Link
                  href={item.href}
                  className="tap-target rounded-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[2px] focus-visible:ring-ring/50"
                >
                  {item.label}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
