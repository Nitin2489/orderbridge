import { LayoutDashboard, type LucideIcon } from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Active only on exactly this path, not its sub-routes. */
  exact?: boolean;
  /**
   * A count shown next to the label (e.g. products in the cart); hidden at 0.
   * Visual only: give the item an `ariaLabel` that states the count.
   */
  badge?: number;
  /** The link's accessible name, when it must say more than `label`. */
  ariaLabel?: string;
  /**
   * Narrows where the item is active: exactly `href`, plus these paths and
   * their sub-routes. Used when another item lives under `href` (the order
   * queue at /desk is not active on /desk/exports).
   */
  activePrefixes?: readonly string[];
}

/**
 * Primary navigation, shared by the desktop and mobile menus.
 *
 * Only routes that exist belong here, so the shell never shows dead links.
 * Feature modules add their entries as their screens are built.
 */
export const primaryNavigation: readonly NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
];

/** Whether a navigation item is active on `pathname`. */
export function isNavItemActive(pathname: string, item: NavItem): boolean {
  if (item.activePrefixes) {
    return (
      pathname === item.href ||
      item.activePrefixes.some((prefix) => isActivePath(pathname, prefix))
    );
  }
  return isActivePath(pathname, item.href, item.exact);
}

/** True when `pathname` is `href` or (unless `exact`) one of its sub-routes. */
export function isActivePath(pathname: string, href: string, exact = false): boolean {
  if (href === "/" || exact) {
    return pathname === href;
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
