"use client";

import {
  FileSpreadsheetIcon,
  InboxIcon,
  ReceiptTextIcon,
  ShoppingCartIcon,
  StoreIcon,
} from "lucide-react";

import { useSession } from "@/features/auth/session-provider";
import { useCart } from "@/features/cart/cart-provider";
import { CART_PATH, CATALOGUE_PATH } from "@/features/cart/paths";
import { DESK_PATH } from "@/features/desk/paths";
import { EXPORTS_PATH } from "@/features/exports/paths";
import { ORDERS_PATH } from "@/features/orders/paths";

import { primaryNavigation, type NavItem } from "./navigation";

/** "Cart, empty", "Cart, 1 product", "Cart, 3 products". */
export function cartNavigationLabel(itemCount: number): string {
  if (itemCount <= 0) {
    return "Cart, empty";
  }
  return `Cart, ${itemCount} ${itemCount === 1 ? "product" : "products"}`;
}

/**
 * The navigation for the signed-in user: retailers also get the catalogue,
 * their orders and their cart (with its product count); the order desk gets
 * its order queue and Tally export. Role checks here only decide
 * what to show; the backend authorises every request.
 */
export function usePrimaryNavigation(): readonly NavItem[] {
  const { user } = useSession();
  const { itemCount } = useCart();

  if (user?.role === "order_desk") {
    return [
      ...primaryNavigation,
      // Active on /desk and on order review pages, not on /desk/exports.
      {
        href: DESK_PATH,
        label: "Order queue",
        icon: InboxIcon,
        activePrefixes: [`${DESK_PATH}/orders`],
      },
      { href: EXPORTS_PATH, label: "Tally export", icon: FileSpreadsheetIcon },
    ];
  }
  if (user?.role !== "retailer") {
    return primaryNavigation;
  }
  return [
    ...primaryNavigation,
    { href: CATALOGUE_PATH, label: "Catalogue", icon: StoreIcon, exact: true },
    { href: ORDERS_PATH, label: "Orders", icon: ReceiptTextIcon },
    {
      href: CART_PATH,
      label: "Cart",
      icon: ShoppingCartIcon,
      badge: itemCount,
      ariaLabel: cartNavigationLabel(itemCount),
    },
  ];
}
