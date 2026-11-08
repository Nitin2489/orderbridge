"use client";

import { DownloadIcon } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useSession } from "@/features/auth/session-provider";
import { cn } from "@/lib/utils";

import { useBatchDownload } from "../api";
import { canVerifyDownloads, downloadErrorMessage } from "../download";
import type { DownloadableBatch } from "../types";

/**
 * Downloads a batch's stored CSV. The file is only saved when its SHA-256
 * matches the batch's recorded checksum; a browser without Web Crypto can
 * only save it unverified, and the button and its result say so.
 */
export function DownloadButton({
  batch,
  className,
}: {
  batch: DownloadableBatch;
  className?: string;
}) {
  const { user } = useSession();
  const download = useBatchDownload(batch);
  const inFlight = useRef(false);
  const statusId = useId();
  // Checked once per render, before any click: the label must say whether
  // the file will be verified.
  const [verifiable] = useState(canVerifyDownloads);

  const start = () => {
    if (!user || inFlight.current || download.isPending) {
      return;
    }
    inFlight.current = true;
    download.mutate(
      { sessionUserId: user.id },
      {
        onSettled: () => {
          inFlight.current = false;
        },
      },
    );
  };

  const error = download.isError ? downloadErrorMessage(download.error) : null;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Button
        type="button"
        variant="outline"
        className="h-11 w-fit"
        disabled={download.isPending}
        aria-describedby={statusId}
        onClick={start}
      >
        {download.isPending ? <Spinner /> : <DownloadIcon aria-hidden="true" />}
        {download.isPending
          ? "Downloading…"
          : verifiable
            ? "Download CSV"
            : "Download CSV (unverified)"}
        <span className="sr-only"> for {batch.batch_number}</span>
      </Button>
      <p id={statusId} role="status" aria-live="polite" className="text-xs">
        {error ? (
          <span role="alert" className="font-medium text-destructive">
            {error}
          </span>
        ) : download.isSuccess ? (
          download.data.verified ? (
            <span className="text-muted-foreground">
              Downloaded. The file matches its SHA-256 checksum.
            </span>
          ) : (
            <span className="font-medium">
              Downloaded without verification: this browser cannot check the file&apos;s SHA-256
              checksum.
            </span>
          )
        ) : !verifiable ? (
          <span className="text-muted-foreground">
            This browser cannot verify downloads. Use HTTPS or a current browser to check the
            file&apos;s checksum.
          </span>
        ) : null}
      </p>
    </div>
  );
}
