import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A field-level validation message. Give it an id and point the field's
 * aria-describedby at it. It is announced as soon as it appears.
 */
function FormMessage({ className, children, ...props }: React.ComponentProps<"p">) {
  if (!children) {
    return null;
  }
  return (
    <p
      role="alert"
      data-slot="form-message"
      className={cn("text-sm font-medium text-destructive", className)}
      {...props}
    >
      {children}
    </p>
  );
}

export { FormMessage };
