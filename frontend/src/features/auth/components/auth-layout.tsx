import type { ReactNode } from "react";

import { BrandMark } from "@/components/layout/brand";

/**
 * Polished frame for sign-in steps in Midnight Blue + Electric Teal:
 * optimized for narrow mobile screens up to desktop displays.
 */
export function AuthLayout({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby="auth-title" className="mx-auto w-full max-w-md px-3 sm:px-0 sm:py-6">
      <div className="rounded-xl border bg-card p-6 shadow-xs sm:p-8">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="inline-flex size-12 items-center justify-center rounded-xl bg-accent p-2 shadow-xs ring-1 ring-ring/30">
            <BrandMark className="size-8" />
          </div>
          <span className="mt-2 text-xs font-semibold tracking-wider text-primary uppercase">
            OrderBridge
          </span>
        </div>

        <h1 id="auth-title" className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          {title}
        </h1>
        {description ? (
          <div className="mt-2 text-sm text-muted-foreground sm:text-base">{description}</div>
        ) : null}
        <div className="mt-6 sm:mt-8">{children}</div>
      </div>
    </section>
  );
}
