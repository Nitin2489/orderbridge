"use client";

import { AlertCircleIcon, CircleCheckIcon, FileSpreadsheetIcon } from "lucide-react";
import Link from "next/link";
import { useRef, useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { Skeleton } from "@/components/ui/skeleton";
import { SignOutButton } from "@/features/auth/components/sign-out-button";
import { useSession } from "@/features/auth/session-provider";
import { formatCount, formatRupees } from "@/features/catalogue/format";

import { useCreateExport, useEligibleExport } from "../api";
import { exportFailure, exportFailureMessage } from "../errors";
import { exportBatchPath } from "../paths";
import { newRequestId } from "../request-id";
import type { CreateExportResult } from "../types";
import { CsvNotice } from "./csv-notice";
import { DownloadButton } from "./download-button";
import { ExportConfirm } from "./export-confirm";
import { ExportHistory, loadErrorMessage } from "./export-history";

function orders(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "order" : "orders"}`;
}

function ExportResult({ result }: { result: CreateExportResult }) {
  const { batch, created } = result;
  return (
    <Alert variant="info" role="status">
      <CircleCheckIcon aria-hidden="true" />
      <AlertTitle>
        {created ? "Export created" : "This export had already been created"}: {batch.batch_number}
      </AlertTitle>
      <AlertDescription>
        <p>
          {orders(batch.order_count)} exported ·{" "}
          <span className="font-semibold tabular-nums">{formatRupees(batch.net_total)}</span>{" "}
          (excl. GST). These orders are now Exported.
        </p>
        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-start">
          <DownloadButton batch={batch} />
          <Button asChild variant="ghost" className="h-11 w-fit text-foreground">
            <Link href={exportBatchPath(batch.id)}>View batch</Link>
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

function ExportWorkspace({ sessionUserId }: { sessionUserId: string }) {
  const eligible = useEligibleExport();
  const create = useCreateExport();
  const [confirming, setConfirming] = useState(false);
  const [idUnavailable, setIdUnavailable] = useState(false);
  // The idempotency key of the export being attempted. Kept after an
  // uncertain outcome so "Retry export" repeats the same request; cleared
  // after any definite outcome or a cancel.
  const requestId = useRef<string | null>(null);
  const inFlight = useRef(false);

  const failure = create.isError ? exportFailure(create.error) : null;
  const retry = failure?.kind === "uncertain";

  const open = () => {
    create.reset();
    setIdUnavailable(false);
    requestId.current = null;
    setConfirming(true);
  };

  const cancel = () => {
    create.reset();
    requestId.current = null;
    setConfirming(false);
  };

  const submit = () => {
    if (inFlight.current || create.isPending) {
      return;
    }
    let clientRequestId = requestId.current;
    if (clientRequestId === null) {
      try {
        clientRequestId = newRequestId();
      } catch {
        setIdUnavailable(true);
        return;
      }
      requestId.current = clientRequestId;
    }
    inFlight.current = true;
    create.mutate(
      { clientRequestId, sessionUserId },
      {
        onSuccess: () => {
          requestId.current = null;
          setConfirming(false);
        },
        onError: (error) => {
          if (exportFailure(error).kind !== "uncertain") {
            requestId.current = null;
            setConfirming(false);
          }
        },
        onSettled: () => {
          inFlight.current = false;
        },
      },
    );
  };

  let waiting: ReactNode;
  if (eligible.isPending) {
    waiting = (
      <>
        <p role="status" className="sr-only">
          Loading orders waiting for export…
        </p>
        <div aria-hidden="true" className="flex flex-col gap-2">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-4 w-64" />
        </div>
      </>
    );
  } else if (eligible.isError) {
    waiting = (
      <Alert variant="destructive" role="alert">
        <AlertCircleIcon aria-hidden="true" />
        <AlertTitle>{loadErrorMessage(eligible.error, "The orders waiting for export")}</AlertTitle>
        <AlertDescription>
          <Button
            type="button"
            variant="outline"
            className="mt-3 h-11 text-foreground"
            onClick={() => void eligible.refetch()}
          >
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (eligible.data.order_count === 0) {
    waiting = (
      <div className="flex flex-col gap-1">
        <p className="text-lg font-semibold">No confirmed orders are waiting for export</p>
        <p className="text-sm text-muted-foreground">
          Orders appear here once the desk confirms them.
        </p>
      </div>
    );
  } else {
    waiting = (
      <div className="flex flex-col gap-4">
        <p className="flex flex-col gap-1">
          <span className="text-2xl font-semibold tracking-tight">
            {orders(eligible.data.order_count)}
          </span>
          <span className="text-sm">
            <span className="font-semibold tabular-nums">
              {formatRupees(eligible.data.net_total)}
            </span>{" "}
            <span className="text-muted-foreground">(excl. GST), confirmed and not yet exported</span>
          </span>
        </p>
        {!confirming ? (
          <Button type="button" size="lg" className="w-fit" onClick={open}>
            <FileSpreadsheetIcon aria-hidden="true" />
            Export to CSV
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-primary">Order desk</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">Tally export</h1>
          <div className="mt-1 max-w-2xl">
            <CsvNotice />
          </div>
        </div>
        <SignOutButton />
      </div>

      <section
        aria-labelledby="waiting-heading"
        className="flex flex-col gap-3 rounded-xl border bg-card p-5 shadow-xs"
      >
        <h2 id="waiting-heading" className="text-base font-semibold">
          Waiting for export
        </h2>
        {waiting}
      </section>

      <div aria-live="polite" className="flex flex-col gap-3">
        {create.isSuccess ? <ExportResult result={create.data} /> : null}
        {failure ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>{exportFailureMessage(failure)}</AlertDescription>
          </Alert>
        ) : null}
        {idUnavailable ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>
              This browser cannot create a safe request id, so nothing was exported. Use a current
              browser over HTTPS.
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      {confirming ? (
        <ExportConfirm
          eligible={eligible.data}
          pending={create.isPending}
          retry={retry}
          onExport={submit}
          onCancel={cancel}
        />
      ) : null}

      <ExportHistory />
    </div>
  );
}

/** /desk/exports: export confirmed orders to a Tally-oriented CSV. */
export function ExportScreen() {
  const { user } = useSession();
  if (!user) {
    return <LoadingState label="Loading…" />;
  }
  // Keyed by the signed-in user: after a session change the workspace
  // starts afresh, so no result, failure, retry offer or request id from
  // the previous session carries over.
  return <ExportWorkspace key={user.id} sessionUserId={user.id} />;
}
