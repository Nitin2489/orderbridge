import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionCheckLoading, WAKE_UP_HINT, WAKE_UP_HINT_DELAY_MS } from "./session-check-loading";

afterEach(() => {
  vi.useRealTimers();
});

describe("SessionCheckLoading", () => {
  it("shows only the spinner at first, then explains a possible wake-up after 3 seconds", () => {
    vi.useFakeTimers();
    render(<SessionCheckLoading />);

    expect(screen.getByRole("status")).toHaveTextContent("Checking your session…");
    expect(screen.queryByText(WAKE_UP_HINT)).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(WAKE_UP_HINT_DELAY_MS - 1));
    expect(screen.queryByText(WAKE_UP_HINT)).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1));
    // Inside the live status region, with the spinner still there; it never
    // claims a failure or timeout.
    expect(screen.getByRole("status")).toHaveTextContent(WAKE_UP_HINT);
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session…");
    expect(screen.getByRole("status")).not.toHaveTextContent(/fail|timed out|error/i);
  });

  it("stops its timer when it is removed", () => {
    vi.useFakeTimers();
    const { unmount } = render(<SessionCheckLoading />);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
