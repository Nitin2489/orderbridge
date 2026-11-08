import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { describeWait, formatCountdown, useSecondsUntil } from "./countdown";

afterEach(() => {
  vi.useRealTimers();
});

describe("formatCountdown", () => {
  it.each([
    [0, "0:00"],
    [5, "0:05"],
    [60, "1:00"],
    [75, "1:15"],
    [900, "15:00"],
  ])("formats %i as %s", (seconds, expected) => {
    expect(formatCountdown(seconds)).toBe(expected);
  });
});

describe("describeWait", () => {
  it.each([
    [1, "1 second"],
    [30, "30 seconds"],
    [60, "1 minute"],
    [61, "2 minutes"],
    [900, "15 minutes"],
    [3600, "60 minutes"],
  ])("describes %i seconds as %s", (seconds, expected) => {
    expect(describeWait(seconds)).toBe(expected);
  });
});

describe("useSecondsUntil", () => {
  it("counts down once a second and stops at zero", () => {
    vi.useFakeTimers();
    const target = Date.now() + 3000;
    const { result } = renderHook(() => useSecondsUntil(target));

    expect(result.current).toBe(3);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(2);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("follows a later deadline at once, as a Retry-After extends it", () => {
    vi.useFakeTimers();
    const start = Date.now();
    const { result, rerender } = renderHook(({ target }) => useSecondsUntil(target), {
      initialProps: { target: start + 10_000 },
    });
    expect(result.current).toBe(10);

    rerender({ target: start + 600_000 });

    expect(result.current).toBe(600);
  });

  it("is zero for no deadline", () => {
    const { result } = renderHook(() => useSecondsUntil(0));

    expect(result.current).toBe(0);
  });
});
