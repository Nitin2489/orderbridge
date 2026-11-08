"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AlertCircleIcon, KeyRoundIcon, ShieldCheckIcon } from "lucide-react";
import { useId, useRef, useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";

import { DEMO_ACCESS_QUERY_KEY, unlockDemoAccess, useDemoAccess } from "../api";
import { unlockDemoAccessErrorMessage } from "../errors";
import type { DemoAccessStatus } from "../types";

/**
 * Reviewer access to the demonstration accounts, below the number step.
 *
 * Shown only when the deployment offers it. The evaluator enters the private
 * access key once; the backend checks it and sets an HttpOnly cookie, after
 * which the sign-in code of a demo account is shown on the next step.
 *
 * The key is read from the field when the form is submitted and the field is
 * cleared straight away: it is never kept in React state, the query cache,
 * storage or the URL.
 */
export function ReviewerAccess() {
  const status = useDemoAccess();
  const queryClient = useQueryClient();
  const keyRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fieldId = useId();
  const panelId = useId();

  // Nothing at all unless the backend says reviewer access is offered; a
  // failed status check is not worth an error on the sign-in page.
  if (!status.data?.enabled) {
    return null;
  }

  if (status.data.active) {
    return (
      <p
        role="status"
        className="mt-6 flex items-start gap-2 rounded-lg border border-dashed bg-secondary/60 p-3 text-sm text-foreground"
      >
        <ShieldCheckIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
        <span>
          <span className="font-medium">Reviewer access is on for this browser.</span> The code
          for a demo account is shown after you continue.
        </span>
      </p>
    );
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const field = keyRef.current;
    const key = field?.value.trim() ?? "";
    if (field) {
      field.value = "";
    }
    if (!key) {
      setError("Enter the reviewer access key.");
      field?.focus();
      return;
    }

    setPending(true);
    setError(null);
    try {
      await unlockDemoAccess(key);
      queryClient.setQueryData<DemoAccessStatus>(DEMO_ACCESS_QUERY_KEY, {
        enabled: true,
        active: true,
      });
    } catch (failure) {
      setError(unlockDemoAccessErrorMessage(failure));
      keyRef.current?.focus();
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="mt-6 border-t pt-4">
      <Button
        type="button"
        variant="ghost"
        className="h-11 w-full justify-start px-2 text-muted-foreground"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((current) => !current)}
      >
        <KeyRoundIcon aria-hidden="true" />
        Reviewer access
      </Button>

      {open ? (
        <form
          id={panelId}
          noValidate
          className="mt-3 flex flex-col gap-3"
          onSubmit={(event) => void submit(event)}
        >
          <p className="text-sm text-muted-foreground">
            This demonstration has no SMS provider. Enter the access key from the submission to
            see the sign-in codes of the demo accounts.
          </p>
          <div className="flex flex-col gap-2">
            <Label htmlFor={fieldId} className="text-sm font-semibold">
              Reviewer access key
            </Label>
            <Input
              ref={keyRef}
              id={fieldId}
              type="password"
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              maxLength={256}
              readOnly={pending}
              aria-invalid={error ? true : undefined}
            />
          </div>

          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertCircleIcon aria-hidden="true" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <Button type="submit" variant="outline" className="h-11" disabled={pending}>
            {pending ? (
              <>
                <Spinner />
                Checking…
              </>
            ) : (
              "Unlock reviewer access"
            )}
          </Button>
        </form>
      ) : null}
    </div>
  );
}
