import { useSyncExternalStore } from "react";

const noSubscription = () => () => undefined;

/**
 * False during server rendering and hydration, true afterwards. For UI that
 * depends on browser-only state (e.g. the stored theme), without setting
 * state in an effect.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
}
