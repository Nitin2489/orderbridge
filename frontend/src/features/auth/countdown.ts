import { useEffect, useState } from "react";

import { secondsUntil } from "./clock";

/** 75 -> "1:15", 600 -> "10:00". */
export function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}

/** A wait in words: 45 -> "45 seconds", 61 -> "2 minutes", 3600 -> "60 minutes". */
export function describeWait(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} ${seconds === 1 ? "second" : "seconds"}`;
  }
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

/**
 * Whole seconds until `timestamp` (epoch milliseconds; 0 or a past time
 * means none), re-rendering once a second until it reaches 0.
 *
 * The value is derived from the clock on every render, so it is correct as
 * soon as `timestamp` changes (for example when a Retry-After extends a
 * countdown); the interval only triggers those re-renders.
 */
export function useSecondsUntil(timestamp: number): number {
  const [, setTick] = useState(0);
  const remaining = secondsUntil(timestamp);

  useEffect(() => {
    if (secondsUntil(timestamp) === 0) {
      return;
    }
    const timer = setInterval(() => {
      setTick((tick) => tick + 1);
      if (secondsUntil(timestamp) === 0) {
        clearInterval(timer);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [timestamp]);

  return remaining;
}
