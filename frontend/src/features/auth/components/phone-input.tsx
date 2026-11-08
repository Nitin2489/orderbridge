import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Indian mobile number input with a fixed +91 prefix. The user types the
 * 10-digit number; pasting a full number such as "+91 90000 00001" also
 * works, because the form normalises the value before sending it.
 */
export function PhoneInput({
  className,
  "aria-invalid": ariaInvalid,
  ...props
}: Omit<React.ComponentProps<"input">, "type">) {
  return (
    <div
      className={cn(
        "flex h-12 sm:h-13 w-full items-stretch overflow-hidden rounded-lg border border-input bg-card shadow-xs transition-[color,box-shadow,border-color] focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50",
        ariaInvalid && "border-destructive ring-destructive/20 focus-within:border-destructive",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex items-center border-r bg-secondary/80 px-3.5 text-base font-semibold text-foreground select-none"
      >
        +91
      </span>
      <input
        type="tel"
        inputMode="numeric"
        autoComplete="tel-national"
        maxLength={16}
        aria-invalid={ariaInvalid}
        className="min-w-0 flex-1 bg-transparent px-3 text-base tracking-wide text-foreground outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
        {...props}
      />
    </div>
  );
}
