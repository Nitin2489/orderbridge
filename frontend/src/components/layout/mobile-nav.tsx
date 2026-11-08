"use client";

import { LogOutIcon, MenuIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Brand } from "@/components/layout/brand";
import { NavBadge } from "@/components/layout/nav-badge";
import { isNavItemActive } from "@/components/layout/navigation";
import { usePrimaryNavigation } from "@/components/layout/use-primary-navigation";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Spinner } from "@/components/ui/spinner";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { useLogout } from "@/features/auth/api";
import { logoutErrorMessage } from "@/features/auth/errors";
import { SIGN_IN_PATH } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-provider";
import type { AuthenticatedUser } from "@/features/auth/types";
import { cn } from "@/lib/utils";

/** Phone and tablet navigation: a menu button opening a slide-in panel. */
export function MobileNav() {
  const pathname = usePathname();
  const navigation = usePrimaryNavigation();
  const { user, status } = useSession();
  // The menu is open for one session only: signing out (or the session
  // ending) closes it, without an effect or a callback from the footer.
  const sessionKey = user?.id ?? null;
  const [openFor, setOpenFor] = useState<string | null | undefined>(undefined);
  const open = openFor !== undefined && openFor === sessionKey;
  const setOpen = (next: boolean) => setOpenFor(next ? sessionKey : undefined);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="md:hidden">
          <MenuIcon className="size-6" aria-hidden="true" />
          <span className="sr-only">Open navigation menu</span>
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="flex flex-col justify-between">
        <div className="flex flex-col gap-4">
          <SheetHeader>
            <SheetTitle asChild>
              <div>
                <Brand />
              </div>
            </SheetTitle>
            <SheetDescription className="sr-only">Main navigation</SheetDescription>
          </SheetHeader>
          <nav aria-label="Primary" className="px-2">
            <ul className="flex flex-col gap-1">
              {navigation.map((item) => {
                const { href, label, icon: Icon, badge, ariaLabel } = item;
                const active = isNavItemActive(pathname, item);
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      aria-label={ariaLabel}
                      onClick={() => setOpen(false)}
                      className={cn(
                        "flex min-h-12 items-center gap-3 rounded-md px-3 text-base font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                        active
                          ? "bg-accent text-accent-foreground"
                          : "text-foreground hover:bg-secondary",
                      )}
                    >
                      <Icon className="size-5" aria-hidden="true" />
                      {label}
                      <NavBadge count={badge} />
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </div>

        {/* Drawer footer: Theme Selector + User Identity / Sign Out */}
        <div className="border-t p-4 flex flex-col gap-4 bg-card/40 rounded-b-xl">
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Theme
            </span>
            <ThemeToggle variant="segmented" className="w-full justify-around" />
          </div>

          {status === "authenticated" && user ? (
            // Keyed by user: the shell stays mounted across sign-out and
            // sign-in, so each session starts with a fresh sign-out state.
            <MobileAccount key={user.id} user={user} />
          ) : status === "unauthenticated" && pathname !== SIGN_IN_PATH ? (
            <Button asChild className="w-full h-11" onClick={() => setOpen(false)}>
              <Link href={SIGN_IN_PATH}>Sign in</Link>
            </Button>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** The signed-in user and the sign-out action in the menu's footer. */
function MobileAccount({ user }: { user: AuthenticatedUser }) {
  const logout = useLogout();
  // Only this sign-out request disables the button; see SessionControls.
  const busy = logout.isPending;
  const displayName = user.retailer?.business_name ?? user.full_name;
  const roleLabel = user.role === "order_desk" ? "Order Desk" : "Retailer";

  return (
    <div className="flex flex-col gap-2 pt-2 border-t">
      <div className="flex flex-col leading-tight">
        <span className="text-sm font-semibold text-foreground truncate" title={displayName}>
          {displayName}
        </span>
        <span className="text-xs font-medium text-primary">{roleLabel}</span>
      </div>
      <Button
        type="button"
        variant="outline"
        className="w-full h-11 justify-start gap-2"
        onClick={() => logout.mutate()}
        disabled={busy}
      >
        {busy ? <Spinner /> : <LogOutIcon className="size-4" aria-hidden="true" />}
        <span>{busy ? "Signing out…" : "Sign out"}</span>
      </Button>
      {logout.isError ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {logoutErrorMessage(logout.error)}
        </p>
      ) : null}
    </div>
  );
}
