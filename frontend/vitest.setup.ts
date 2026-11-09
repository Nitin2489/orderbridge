import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no matchMedia; the theme provider reads the system preference.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

// Vitest globals are off, so Testing Library cannot register this itself.
afterEach(async () => {
  cleanup();
  // The saved cart and theme live in localStorage: no test sees another's.
  // (Node-environment tests, such as the e2e helpers, have no window.)
  if (typeof window !== "undefined") {
    window.localStorage.clear();
  }
  const navigation = await import("./src/test/navigation-mock");
  navigation.setPathname("/");
});

// Components use the App Router hooks; tests drive and inspect them through
// src/test/navigation-mock.ts.
vi.mock("next/navigation", async () => {
  const navigation = await import("./src/test/navigation-mock");
  return {
    useRouter: () => navigation.mockRouter,
    usePathname: () => navigation.getPathname(),
  };
});
