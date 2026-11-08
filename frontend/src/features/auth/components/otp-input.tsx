"use client";

import { useEffect, useRef, type ClipboardEvent, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

import { OTP_LENGTH } from "../schemas";

export const EMPTY_OTP: readonly string[] = Array.from({ length: OTP_LENGTH }, () => "");

interface OtpInputProps {
  /** One entry per box: a single digit or "". */
  value: readonly string[];
  onChange: (value: string[]) => void;
  /** id of the visible label for the group. */
  labelledBy: string;
  /** id(s) of help or error text. */
  describedBy?: string;
  invalid?: boolean;
  disabled?: boolean;
  /** Focus the first box on mount. */
  autoFocus?: boolean;
  /**
   * Increment to move focus to the first box, e.g. after a rejected code.
   * The focus happens after React commits that render, and waits until the
   * boxes are enabled, so it lands even if the parent clears the value in
   * the same update.
   */
  focusRequest?: number;
}

/**
 * A six-box one-time code input.
 *
 * Typing a digit moves to the next box; Backspace on an empty box clears the
 * previous one and moves back; arrow keys move between boxes; pasting (or an
 * SMS autofill) spreads the digits across the boxes. Non-digits are ignored.
 */
export function OtpInput({
  value,
  onChange,
  labelledBy,
  describedBy,
  invalid = false,
  disabled = false,
  autoFocus = false,
  focusRequest = 0,
}: OtpInputProps) {
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  // The last focusRequest acted on. Starts at the initial value, so a
  // request only counts once it changes after mount.
  const handledFocusRequest = useRef(focusRequest);

  useEffect(() => {
    if (autoFocus) {
      inputs.current[0]?.focus();
    }
  }, [autoFocus]);

  // A disabled input cannot take focus. If the request is committed while
  // the boxes are still disabled, this runs again when `disabled` turns
  // false; the ref ensures each request is honoured exactly once.
  useEffect(() => {
    if (disabled || focusRequest === handledFocusRequest.current) {
      return;
    }
    handledFocusRequest.current = focusRequest;
    inputs.current[0]?.focus();
  }, [focusRequest, disabled]);

  const focusBox = (index: number) => {
    const target = inputs.current[Math.max(0, Math.min(OTP_LENGTH - 1, index))];
    target?.focus();
    target?.select();
  };

  /** Write `digits` starting at box `start`; return the index after the last one. */
  const fill = (start: number, digits: string): number => {
    const next = [...value];
    let index = start;
    for (const digit of digits) {
      if (index >= OTP_LENGTH) {
        break;
      }
      next[index] = digit;
      index += 1;
    }
    onChange(next);
    return index;
  };

  const handleChange = (index: number, raw: string) => {
    const digits = raw.replace(/\D/g, "");

    if (digits === "") {
      // Cleared (e.g. Backspace on a filled box) or a non-digit was typed.
      if (raw === "") {
        const next = [...value];
        next[index] = "";
        onChange(next);
      }
      return;
    }

    // One digit typed, or several from autofill: a full code fills from the
    // first box, anything else from the current one.
    const start = digits.length >= OTP_LENGTH ? 0 : index;
    const end = fill(start, digits.slice(0, OTP_LENGTH));
    focusBox(end);
  };

  const handlePaste = (index: number, event: ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault();
    const digits = event.clipboardData.getData("text").replace(/\D/g, "");
    if (digits === "") {
      return;
    }
    const start = digits.length >= OTP_LENGTH ? 0 : index;
    const end = fill(start, digits.slice(0, OTP_LENGTH));
    focusBox(end);
  };

  const handleKeyDown = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "Backspace":
        if (value[index] === "" && index > 0) {
          event.preventDefault();
          const next = [...value];
          next[index - 1] = "";
          onChange(next);
          focusBox(index - 1);
        }
        break;
      case "ArrowLeft":
        event.preventDefault();
        focusBox(index - 1);
        break;
      case "ArrowRight":
        event.preventDefault();
        focusBox(index + 1);
        break;
      default:
        break;
    }
  };

  return (
    <div
      role="group"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      className="flex justify-between gap-1.5 sm:gap-2.5"
    >
      {value.map((digit, index) => (
        <input
          // The boxes are a fixed set; their position is their identity.
          key={index}
          ref={(element) => {
            inputs.current[index] = element;
          }}
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          // Lets phones offer the code from an incoming SMS (first box only).
          autoComplete={index === 0 ? "one-time-code" : "off"}
          maxLength={index === 0 ? OTP_LENGTH : 1}
          aria-label={`Digit ${index + 1} of ${OTP_LENGTH}`}
          aria-invalid={invalid || undefined}
          disabled={disabled}
          value={digit}
          onChange={(event) => handleChange(index, event.target.value)}
          onPaste={(event) => handlePaste(index, event)}
          onKeyDown={(event) => handleKeyDown(index, event)}
          onFocus={(event) => event.target.select()}
          className={cn(
            "h-12 w-full min-w-0 max-w-11 rounded-lg border border-input bg-card text-center text-lg font-semibold tabular-nums shadow-xs transition-[color,box-shadow,border-color,background-color] outline-none sm:h-14 sm:max-w-13 sm:text-xl focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
            digit && !invalid && "border-primary/50 bg-accent/15",
            invalid && "border-destructive text-destructive ring-destructive/20 focus-visible:border-destructive",
          )}
        />
      ))}
    </div>
  );
}
