"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircleIcon } from "lucide-react";
import { useForm, useWatch } from "react-hook-form";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FormMessage } from "@/components/ui/form-message";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";

import { formatCountdown, useSecondsUntil } from "../countdown";
import {
  normalizePhoneNumber,
  phoneFormSchema,
  type PhoneFormInput,
  type PhoneFormValues,
} from "../schemas";
import { PhoneInput } from "./phone-input";

interface PhoneFormProps {
  /** National number to prefill, e.g. after "Change number". */
  defaultPhone: string;
  pending: boolean;
  /** A request failure to show, already turned into a user-facing message. */
  error: string | null;
  /**
   * The normalised number `error` belongs to. The error is shown only while
   * that number is entered, so one number's 429 is not shown for another.
   */
  errorPhoneNumber: string | null;
  /**
   * Per normalised number: when the backend allows another code request
   * (epoch milliseconds), after a 429 with Retry-After.
   */
  retryAt: Readonly<Record<string, number>>;
  /** Called with the normalised +91 number. */
  onSubmit: (phoneNumber: string) => void;
}

/** Step 1: the mobile number. */
export function PhoneForm({
  defaultPhone,
  pending,
  error,
  errorPhoneNumber,
  retryAt,
  onSubmit,
}: PhoneFormProps) {
  const form = useForm<PhoneFormInput, unknown, PhoneFormValues>({
    resolver: zodResolver(phoneFormSchema),
    defaultValues: { phone: defaultPhone },
  });

  // Only the number that was rate limited waits; another number can still
  // be tried (the server applies its own limits to it).
  const typed = useWatch({ control: form.control, name: "phone" });
  const normalised = normalizePhoneNumber(typed ?? "");
  const waitSeconds = useSecondsUntil(normalised ? (retryAt[normalised] ?? 0) : 0);
  const waiting = waitSeconds > 0;
  const shownError = error && normalised === errorPhoneNumber ? error : null;

  const fieldError = form.formState.errors.phone?.message;
  const describedBy = fieldError ? "phone-hint phone-error" : "phone-hint";

  return (
    <form
      noValidate
      className="flex flex-col gap-6"
      onSubmit={form.handleSubmit((values) => {
        if (!waiting) {
          onSubmit(values.phone);
        }
      })}
    >
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="phone" className="text-sm font-semibold">
            Mobile number
          </Label>
          <span className="text-xs font-medium text-muted-foreground">Step 1 of 2</span>
        </div>
        <PhoneInput
          id="phone"
          placeholder="98765 43210"
          autoFocus
          readOnly={pending}
          aria-invalid={fieldError ? true : undefined}
          aria-describedby={describedBy}
          {...form.register("phone")}
        />
        <p id="phone-hint" className="text-xs text-muted-foreground sm:text-sm">
          We will send a 6-digit sign-in code to this number.
        </p>
        <FormMessage id="phone-error">{fieldError}</FormMessage>
      </div>

      {shownError ? (
        <Alert variant="destructive" role="alert">
          <AlertCircleIcon aria-hidden="true" />
          <AlertDescription>{shownError}</AlertDescription>
        </Alert>
      ) : null}

      <Button
        type="submit"
        size="lg"
        className="w-full font-medium"
        disabled={pending || waiting}
      >
        {pending ? (
          <>
            <Spinner />
            Sending code…
          </>
        ) : waiting ? (
          `Try again in ${formatCountdown(waitSeconds)}`
        ) : (
          "Continue"
        )}
      </Button>
    </form>
  );
}
