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
import { formatIndiaDateTime } from "@/features/catalogue/format";
import { newRequestId } from "@/features/exports/request-id";
import { statusInfo } from "@/features/orders/status";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { cn } from "@/lib/utils";

import { useCreateCapture, useRetailerDirectory } from "../api";
import { captureFailure } from "../errors";
import { capturePath, capturesPath, type CaptureRole } from "../paths";
import type { PossibleDuplicate } from "../types";
import { captureStatusLabel } from "./capture-status-badge";

const MAX_MESSAGE = 2000;

/**
 * The order desk is about to capture a message this retailer's recent capture
 * already holds. Nothing has been stored yet; the desk either goes back or
 * confirms it is a separate order.
 */
function DuplicateWarning({
  retailerName,
  duplicates,
  busy,
  onBack,
  onContinue,
}: {
  retailerName: string;
  duplicates: PossibleDuplicate[];
  busy: boolean;
  onBack: () => void;
  onContinue: () => void;
}) {
  return (
    <section
      role="alert"
      aria-labelledby="duplicate-heading"
      className="flex flex-col gap-3 rounded-xl border border-amber-500/50 bg-amber-50 p-4 text-sm text-amber-950 dark:bg-amber-950/30 dark:text-amber-100"
    >
      <h2 id="duplicate-heading" className="text-base font-semibold">
        Possible duplicate
      </h2>
      <p>
        {retailerName} already has {duplicates.length === 1 ? "a capture" : "captures"} with the
        same message from the last 24 hours. Check {duplicates.length === 1 ? "it" : "them"} before
        creating another, so the same order is not entered twice.
      </p>
      <ul className="flex flex-col gap-1">
        {duplicates.map((duplicate) => (
          <li key={duplicate.capture_id}>
            <Link
              href={capturePath("order_desk", duplicate.capture_id)}
              className="font-medium underline underline-offset-4"
            >
              Captured {formatIndiaDateTime(duplicate.created_at) ?? "recently"}
            </Link>{" "}
            · {captureStatusLabel(duplicate.status)}
            {duplicate.order
              ? ` · Order ${duplicate.order.order_number} (${statusInfo(duplicate.order.status).label})`
              : " · No order yet"}
          </li>
        ))}
      </ul>
      <p>If this is a separate order, for example a genuine repeat, you can create it anyway.</p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="button" variant="outline" className="h-11" onClick={onBack} disabled={busy}>
          Go back and review
        </Button>
        <Button type="button" className="h-11" onClick={onContinue} disabled={busy}>
          {busy ? <Spinner /> : null}
          Create anyway
        </Button>
      </div>
    </section>
  );
}

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
  const [duplicates, setDuplicates] = useState<PossibleDuplicate[] | null>(null);
  // Kept for a manual retry after an uncertain outcome.
  const requestKey = useRef<string | null>(null);
  const isDesk = role === "order_desk";

  const submit = (allowDuplicate = false) => {
    setFailure(null);
    setDuplicates(null);
    const blank = message.trim() === "";
    setMessageError(
      blank ? (isDesk ? "Paste or type the order as received." : "Paste your order message.") : null,
    );
    setRetailerError(isDesk && !retailer ? "Choose the retailer this order is for." : null);
    if (blank || (isDesk && !retailer)) {
      return;
    }
    requestKey.current ??= newRequestId();
    create.mutate(
      {
        message,
        clientRequestId: requestKey.current,
        retailerId: isDesk ? (retailer?.id ?? null) : null,
        allowDuplicate,
        sessionUserId,
      },
      {
        onSuccess: (capture) => {
          requestKey.current = null;
          router.push(capturePath(role, capture.id));
        },
        onError: (error) => {
          const result = captureFailure(error);
          if (result.kind === "possible_duplicate") {
            // Nothing was stored: the same request id is reused if the desk
            // creates it anyway.
            setDuplicates(result.duplicates);
            return;
          }
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
        className="tap-target inline-flex w-fit items-center gap-1 rounded-sm text-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <ArrowLeftIcon className="size-4" aria-hidden="true" />
        All captured messages
      </Link>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Capture a message</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {isDesk
            ? "Paste or type the order as received: from WhatsApp, a phone call, a salesman's notebook or anywhere else. "
            : "Paste your order message exactly as you wrote it, for example from WhatsApp. "}
          It is stored unchanged. OrderBridge proposes lines and products for you to check; nothing
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
            <Label htmlFor="capture-retailer">Retailer this order is for</Label>
            <RetailerPicker
              value={retailer}
              onChange={(next) => {
                setRetailer(next);
                setRetailerError(null);
                setDuplicates(null);
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
              setDuplicates(null);
            }}
            className="w-full rounded-md border border-input bg-card px-3 py-2 text-base shadow-xs outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 aria-invalid:border-destructive"
          />
          <p id="capture-message-count" className="text-xs text-muted-foreground">
            {message.length}/{MAX_MESSAGE} characters
          </p>
          <FormMessage id="capture-message-error">{messageError}</FormMessage>
        </div>

        {duplicates && retailer ? (
          <DuplicateWarning
            retailerName={retailer.name}
            duplicates={duplicates}
            busy={create.isPending}
            onBack={() => setDuplicates(null)}
            onContinue={() => submit(true)}
          />
        ) : null}

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
