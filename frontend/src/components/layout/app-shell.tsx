import type { ReactNode } from "react";

import { Brand } from "@/components/layout/brand";
import { MainNav } from "@/components/layout/main-nav";
import { MobileNav } from "@/components/layout/mobile-nav";
import { RetailerMobileNav } from "@/components/layout/retailer-mobile-nav";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { SessionControls } from "@/features/auth/components/session-controls";

const CONTAINER = "mx-auto w-full max-w-6xl px-4 sm:px-6 lg:px-8";

/** Page frame: skip link, sticky header with navigation, main area, footer, and retailer bottom nav. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[60] focus:rounded-md focus:bg-card focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:shadow-md focus:outline-none focus:ring-[3px] focus:ring-ring/50"
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75">
        <div className={`${CONTAINER} flex h-16 items-center gap-3 sm:gap-4`}>
          <MobileNav />
          <Brand />
          <MainNav className="ml-4 md:ml-6" />
          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <ThemeToggle className="hidden sm:inline-flex" />
            <SessionControls />
          </div>
        </div>
      </header>

      <main
        id="main-content"
        tabIndex={-1}
        className={`${CONTAINER} flex-1 py-8 outline-none sm:py-12`}
      >
        {children}
      </main>

      <footer className="border-t">
        <div
          className={`${CONTAINER} flex flex-col gap-1 py-6 text-sm text-muted-foreground sm:flex-row sm:justify-between`}
        >
          <p>OrderBridge</p>
          <p>Ordering and order operations for FMCG distribution</p>
        </div>
      </footer>

      {/* Mobile bottom navigation dock for Kirana retailers (retailer role only) */}
      <RetailerMobileNav />
    </div>
  );
}
