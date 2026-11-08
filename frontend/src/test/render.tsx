import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { SessionProvider } from "@/features/auth/session-provider";
import { CartProvider } from "@/features/cart/cart-provider";
import { RepeatAttemptsProvider } from "@/features/orders/repeat-attempts";
import { ThemeProvider } from "@/providers/theme-provider";

export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
}

/** Render inside the same providers as the app, with a fresh cache. */
export function renderWithSession(ui: ReactElement, queryClient = makeTestQueryClient()) {
  const result = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
      <SessionProvider>
        <CartProvider>
          <RepeatAttemptsProvider>{ui}</RepeatAttemptsProvider>
        </CartProvider>
      </SessionProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}
