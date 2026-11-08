"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NavBadge } from "@/components/layout/nav-badge";
import { isNavItemActive } from "@/components/layout/navigation";
import { usePrimaryNavigation } from "@/components/layout/use-primary-navigation";
import { cn } from "@/lib/utils";

/** Desktop navigation (hidden below the md breakpoint). */
export function MainNav({ className }: { className?: string }) {
  const pathname = usePathname();
  const navigation = usePrimaryNavigation();

  return (
    <nav aria-label="Primary" className={cn("hidden md:block", className)}>
      <ul className="flex items-center gap-1">
        {navigation.map((item) => {
          const { href, label, icon: Icon, badge, ariaLabel } = item;
          const active = isNavItemActive(pathname, item);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                aria-label={ariaLabel}
                className={cn(
                  "inline-flex h-10 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                <Icon className="size-4" aria-hidden="true" />
                {label}
                <NavBadge count={badge} />
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
