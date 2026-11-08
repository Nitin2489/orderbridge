import { vi } from "vitest";

/** Stand-in for next/navigation's router, wired up in vitest.setup.ts. */
export const mockRouter = {
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  prefetch: vi.fn(),
};

let pathname = "/";

export function setPathname(next: string): void {
  pathname = next;
}

export function getPathname(): string {
  return pathname;
}
