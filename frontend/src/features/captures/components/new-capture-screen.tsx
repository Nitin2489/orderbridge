"use client";

import { AlertCircleIcon, ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FormMessage } from "@/components/ui/form-message";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { Spinner } from "@/components/ui/spinner";
import { useSession } from "@/features/auth/session-provider";
import { newRequestId } from "@/features/exports/request-id";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { cn } from "@/lib/utils";

import { useCreateCapture, useRetailerDirectory } from "../api";
import { captureFailure } from "../errors";
import { capturePath, capturesPath, type CaptureRole } from "../paths";

const MAX_MESSAGE = 2000;

function RetailerPicker({
  value,
  onChange,
  invalid,
}: {
  value: { id: string; name: string } | null;
  onChange: (retailer: { id: string; name: string } | null) => void;
  invalid: boolean;
}) {
  const [search, setSearch] = useState("");
  const query = useDebouncedValue(search.trim().slice(0, 100), 250);
  const retailers = useRetailerDirectory(query, value === null);

  if (value) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-2">
        <span className="font-medium">{value.name}</span>
        <Button type="button" variant="ghost" className="h-11" onClick={() => onChange(null)}>
          Change retailer
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <Input
        id="capture-retailer"
        type="search"
        placeholder="Search by shop name"
        value={search}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={invalid ? "capture-retailer-error" : undefined}
        onChange={(event) => setSearch(event.target.value)}
      />
      {retailers.isPending ? (
        <p className="text-sm text-muted-foreground">Loading retailers…</p>
      ) : retailers.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Retailers could not be loaded.{" "}
          <button type="button" className="underline" onClick={() => void retailers.refetch()}>
            Try again
          </button>
        </p>
      ) : retailers.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No retailer matches “{query}”.</p>
      ) : (
        <ul aria-label="Retailers" className="flex flex-col gap-1">
          {retailers.data.map((retailer) => (
            <li key={retailer.id}>
              <button
                type="button"
                disabled={!retailer.is_active}
                onClick={() => onChange({ id: retailer.id, name: retailer.business_name })}
                className={cn(
                  "flex min-h-11 w-full items-center justify-between rounded-md border px-3 text-left text-sm outline-none hover:bg-secondary/60 focus-visible:ring-[3px] focus-visible:ring-ring/50",
                  !retailer.is_active && "cursor-not-allowed opacity-60",
                )}
              >
                <span>{retailer.business_name}</span>
                {!retailer.is_active ? <span className="text-xs">Inactive</span> : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewCaptureForm({ role, sessionUserId }: { role: CaptureRole; sessionUserId: string }) {
  const router = useRouter();
  const create = useCreateCapture();
  const [message, setMessage] = useState("");
  const [retailer, setRetailer] = useState<{ id: string; name: string } | null>(null);
  const [messageError, setMessageError] = useState<string | null>(null);
  const [retailerError, setRetailerError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  // Kept for a manual retry after an uncertain outcome.
  const requestKey = useRef<string | null>(null);
  const isDesk = role === "order_desk";

  const submit = () => {
    setFailure(null);
    const blank = message.trim() === "";
    setMessageError(blank ? "Paste the message the retailer sent." : null);
    setRetailerError(isDesk && !retailer ? "Choose the retailer who sent it." : null);
    if (blank || (isDesk && !retailer)) {
      return;
    }
    requestKey.current ??= newRequestId();
    create.mutate(
      {
        message,
        clientRequestId: requestKey.current,
        retailerId: isDesk ? (retailer?.id ?? null) : null,
        sessionUserId,
      },
      {
        onSuccess: (capture) => {
          requestKey.current = null;
          router.push(capturePath(role, capture.id));
        },
        onError: (error) => {
          const result = captureFailure(error);
          if (result.kind !== "uncertain") {
            requestKey.current = null;
          }
          setFailure(result.message);
        },
      },
    );
  };

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <Link
        href={capturesPath(role)}
        className="inline-flex w-fit items-center gap-1 rounded-sm text-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <ArrowLeftIcon className="size-4" aria-hidden="true" />
        All captured messages
      </Link>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Capture a message</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste the order exactly as it was sent, for example from WhatsApp. It is stored
          unchanged. OrderBridge proposes lines with rule-based matching, not an AI model; nothing
          is ordered until every line is decided.
        </p>
      </div>

      <form
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        {isDesk ? (
          <div className="flex flex-col gap-2">
            <Label htmlFor="capture-retailer">Retailer who sent it</Label>
            <RetailerPicker
              value={retailer}
              onChange={(next) => {
                setRetailer(next);
                setRetailerError(null);
              }}
              invalid={retailerError !== null}
            />
            <FormMessage id="capture-retailer-error">{retailerError}</FormMessage>
          </div>
        ) : null}

        <div className="flex flex-col gap-2">
          <Label htmlFor="capture-message">Message</Label>
          <textarea
            id="capture-message"
            rows={6}
            maxLength={MAX_MESSAGE}
            value={message}
            disabled={create.isPending}
            placeholder="bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti"
            aria-invalid={messageError ? true : undefined}
            aria-describedby="capture-message-count capture-message-error"
            onChange={(event) => {
              setMessage(event.target.value);
              setMessageError(null);
            }}
            className="w-full rounded-md border border-input bg-card px-3 py-2 text-base shadow-xs outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 aria-invalid:border-destructive"
          />
          <p id="capture-message-count" className="text-xs text-muted-foreground">
            {message.length}/{MAX_MESSAGE} characters
          </p>
          <FormMessage id="capture-message-error">{messageError}</FormMessage>
        </div>

        {failure ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>{failure}</AlertDescription>
          </Alert>
        ) : null}

        <Button type="submit" size="lg" className="w-full sm:w-fit" disabled={create.isPending}>
          {create.isPending ? (
            <>
              <Spinner />
              Reading the message…
            </>
          ) : (
            "Read message"
          )}
        </Button>
      </form>
    </div>
  );
}

/** Capture a new message. Keyed by the signed-in user. */
export function NewCaptureScreen({ role }: { role: CaptureRole }) {
  const { user } = useSession();
  if (!user) {
    return <LoadingState label="Loading…" />;
  }
  return <NewCaptureForm key={user.id} role={role} sessionUserId={user.id} />;
}
