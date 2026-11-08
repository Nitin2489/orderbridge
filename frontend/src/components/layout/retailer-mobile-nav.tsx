"use client";

import {
  MessageSquareTextIcon,
  ReceiptTextIcon,
  ShoppingCartIcon,
  StoreIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NavBadge } from "@/components/layout/nav-badge";
import { cartNavigationLabel } from "@/components/layout/use-primary-navigation";
import { useSession } from "@/features/auth/session-provider";
import { useCart } from "@/features/cart/cart-provider";
import { CAPTURE_PATH, CART_PATH, CATALOGUE_PATH } from "@/features/cart/paths";
import { ORDERS_PATH } from "@/features/orders/paths";
import { cn } from "@/lib/utils";

interface BottomNavItem {
  href: string;
  label: string;
  icon: typeof StoreIcon;
  exact?: boolean;
  badge?: number;
  ariaLabel?: string;
}

/**
 * Mobile bottom navigation dock for retailers.
 * Strictly retailer-only: never rendered for Order Desk or unauthenticated sessions.
 * Provides comfortable >=48px touch targets for kirana shop owners on mobile devices.
 */
export function RetailerMobileNav() {
  const { user, status } = useSession();
  const pathname = usePathname();
  const { itemCount } = useCart();

  // Strict role check: retailer role only
  if (status !== "authenticated" || user?.role !== "retailer") {
    return null;
  }

  const items: BottomNavItem[] = [
    {
      href: CATALOGUE_PATH,
      label: "Catalogue",
      icon: StoreIcon,
      exact: true,
    },
    {
      href: CAPTURE_PATH,
      label: "Capture",
      icon: MessageSquareTextIcon,
    },
    {
      href: ORDERS_PATH,
      label: "Orders",
      icon: ReceiptTextIcon,
    },
    {
      href: CART_PATH,
      label: "Cart",
      icon: ShoppingCartIcon,
      badge: itemCount > 0 ? itemCount : undefined,
      ariaLabel: cartNavigationLabel(itemCount),
    },
  ];

  return (
    <>
      {/* Spacer to push page content above the fixed navigation bar on mobile */}
      <div className="h-16 md:hidden shrink-0" aria-hidden="true" />

      <nav
        aria-label="Retailer mobile navigation"
        className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/85 md:hidden shadow-lg pb-[max(0.25rem,env(safe-area-inset-bottom))]"
      >
        <ul className="flex h-16 items-center justify-around px-2">
          {items.map((item) => {
            const { href, label, icon: Icon, exact, badge, ariaLabel } = item;
            const active = exact ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);

            return (
              <li key={href} className="flex-1">
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  aria-label={ariaLabel}
                  className={cn(
                    "flex min-h-[52px] w-full flex-col items-center justify-center gap-1 rounded-md py-1 text-[11px] font-medium transition-colors outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
                    active
                      ? "text-primary font-semibold"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <div className="relative inline-flex items-center justify-center">
                    <Icon className="size-5" aria-hidden="true" />
                    {badge ? (
                      <span className="absolute -top-1 -right-2.5">
                        <NavBadge count={badge} />
                      </span>
                    ) : null}
                  </div>
                  <span>{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
