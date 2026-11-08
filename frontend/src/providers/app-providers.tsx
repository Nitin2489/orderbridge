"use client";

import type { ReactNode } from "react";

import { SessionProvider } from "@/features/auth/session-provider";
import { CartProvider } from "@/features/cart/cart-provider";
import { RepeatAttemptsProvider } from "@/features/orders/repeat-attempts";
import { QueryProvider } from "@/providers/query-provider";
import { ThemeProvider } from "@/providers/theme-provider";

/** All client-side providers, composed once for the root layout. */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <QueryProvider>
        <SessionProvider>
          {/* Below the session: the cart belongs to the signed-in retailer. */}
          <CartProvider>
            <RepeatAttemptsProvider>{children}</RepeatAttemptsProvider>
          </CartProvider>
        </SessionProvider>
      </QueryProvider>
    </ThemeProvider>
  );
}
