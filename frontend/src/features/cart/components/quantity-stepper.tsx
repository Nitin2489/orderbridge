"use client";

import { MinusIcon, PlusIcon } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { unitLabel } from "@/features/catalogue/format";
import { cn } from "@/lib/utils";

import { MAX_LINE_QUANTITY, MIN_LINE_QUANTITY, clampQuantity } from "../cart-state";

interface QuantityStepperProps {
  productName: string;
  /** The product's ordering unit, e.g. "case": quantities count these. */
  unitName: string;
  value: number;
  onChange: (quantity: number) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * Decrease / type / increase a quantity, kept within 1 to 10,000 (the
 * backend's limits). Typing is committed on blur or Enter; an empty or
 * invalid entry reverts to the current quantity.
 */
export function QuantityStepper({
  productName,
  unitName,
  value,
  onChange,
  disabled = false,
  className,
}: QuantityStepperProps) {
  const [draft, setDraft] = useState<string | null>(null);

  const commit = () => {
    if (draft === null) {
      return;
    }
    const parsed = Number.parseInt(draft, 10);
    if (!Number.isNaN(parsed)) {
      onChange(clampQuantity(parsed));
    }
    setDraft(null);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      setDraft(null);
    }
  };

  return (
    <div
      role="group"
      aria-label={`Quantity of ${productName}`}
      className={cn("flex items-center gap-2", className)}
    >
      <Button
        type="button"
        variant="outline"
        size="icon"
        aria-label={`Decrease quantity of ${productName}`}
        disabled={disabled || value <= MIN_LINE_QUANTITY}
        onClick={() => onChange(clampQuantity(value - 1))}
      >
        <MinusIcon aria-hidden="true" />
      </Button>
      <input
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        aria-label={`Quantity of ${productName}, in ${unitLabel(unitName, 2)}`}
        disabled={disabled}
        value={draft ?? String(value)}
        onChange={(event) => setDraft(event.target.value.replace(/\D/g, "").slice(0, 5))}
        onBlur={commit}
        onKeyDown={handleKeyDown}
        className="h-11 w-16 rounded-md border border-input bg-card text-center text-base tabular-nums shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
      />
      <Button
        type="button"
        variant="outline"
        size="icon"
        aria-label={`Increase quantity of ${productName}`}
        disabled={disabled || value >= MAX_LINE_QUANTITY}
        onClick={() => onChange(clampQuantity(value + 1))}
      >
        <PlusIcon aria-hidden="true" />
      </Button>
      <span className="text-sm text-muted-foreground">{unitLabel(unitName, value)}</span>
    </div>
  );
}
