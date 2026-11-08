/**
 * Wall-clock helpers, kept out of components so render code stays pure and
 * tests can control time with fake timers.
 */
export function now(): number {
  return Date.now();
}

/** Whole seconds until `timestamp`, never negative. */
export function secondsUntil(timestamp: number): number {
  return Math.max(0, Math.ceil((timestamp - Date.now()) / 1000));
}
