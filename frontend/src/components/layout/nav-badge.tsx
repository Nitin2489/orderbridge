/**
 * A small count next to a navigation label. Visual only: the link's
 * aria-label (NavItem.ariaLabel) states the count, so screen readers hear
 * one clean name such as "Cart, 2 products".
 */
export function NavBadge({ count }: { count: number | undefined }) {
  if (!count) {
    return null;
  }
  return (
    <span
      aria-hidden="true"
      className="inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground tabular-nums"
    >
      {count}
    </span>
  );
}
