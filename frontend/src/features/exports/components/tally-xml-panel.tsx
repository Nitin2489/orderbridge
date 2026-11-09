"use client";

import { FileCodeIcon } from "lucide-react";
import { useId, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useSession } from "@/features/auth/session-provider";

import {
  type TallyMasters,
  useTallyMasters,
  useTallyXmlDownload,
  xmlDownloadErrorMessage,
} from "../tally-xml";

function NameList({ label, names }: { label: string; names: string[] }) {
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-sm font-medium">{label}</h4>
      <ul className="list-disc pl-5 text-sm">
        {names.map((name) => (
          <li key={name} className="break-words">
            {name}
          </li>
        ))}
      </ul>
    </div>
  );
}

function MastersChecklist({ masters }: { masters: TallyMasters }) {
  const ledgers = [
    `${masters.sales_ledger ?? "Sales ledger (not configured)"} (sales)`,
    ...(masters.discount_needed
      ? [`${masters.discount_ledger ?? "Discount ledger (not configured)"} (discounts)`]
      : []),
  ];
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">Masters needed in Tally</h3>
      <p className="text-sm text-muted-foreground">
        Create these in your Tally company with exactly these names before importing. OrderBridge
        cannot see your Tally data; Tally&apos;s import report lists anything missing.
      </p>
      <NameList label={`Voucher type`} names={[masters.voucher_type]} />
      <NameList label="Ledgers" names={ledgers} />
      <NameList label="Party ledgers (Sundry Debtors)" names={masters.party_ledgers} />
      <NameList
        label="Stock items"
        names={masters.stock_items.map((item) => `${item.name} (unit: ${item.unit})`)}
      />
      <ul className="list-disc pl-5 text-sm text-muted-foreground">
        <li>
          Turn on separate actual and billed quantities, so free goods leave stock without being
          charged.
        </li>
        <li>
          Set the voucher type&apos;s numbering to Manual or Automatic (Manual Override) to keep
          the order numbers.
        </li>
        <li>Import in Tally from Import, then Transactions.</li>
      </ul>
    </div>
  );
}

/**
 * TallyPrime XML sales vouchers for a batch: a download and the list of
 * Tally masters they refer to. GST is not included.
 */
export function TallyXmlPanel({ batch }: { batch: { id: string; batch_number: string } }) {
  const { user } = useSession();
  const masters = useTallyMasters(batch.id);
  const download = useTallyXmlDownload(batch);
  const inFlight = useRef(false);
  const statusId = useId();

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

  const missing = masters.data?.missing_settings ?? [];
  const error = download.isError ? xmlDownloadErrorMessage(download.error) : null;

  return (
    <section
      aria-labelledby="tally-xml-heading"
      className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs"
    >
      <div className="flex flex-col gap-1">
        <h2 id="tally-xml-heading" className="text-base font-semibold">
          Tally XML Export (Experimental)
        </h2>
        <p className="text-sm text-muted-foreground">
          One Sales voucher per order, built from this export. Follows Tally&apos;s documented
          XML voucher format; not yet tested in TallyPrime. Free cases are recorded as actual but
          not billed quantity; discounts go to the discount ledger. Amounts exclude GST, which is
          added in Tally.
        </p>
        <p className="text-sm font-medium">
          Import each batch only once: importing the same file again creates duplicate vouchers
          in Tally.
        </p>
      </div>

      {missing.length > 0 ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          Not available yet: set {missing.join(" and ")} on the server to the ledger names in
          Tally.
        </p>
      ) : null}

      <div className="flex flex-col gap-1">
        <Button
          type="button"
          variant="outline"
          className="h-11 w-fit"
          disabled={download.isPending || missing.length > 0}
          aria-describedby={statusId}
          onClick={start}
        >
          {download.isPending ? <Spinner /> : <FileCodeIcon aria-hidden="true" />}
          {download.isPending ? "Downloading…" : "Download Tally XML"}
          <span className="sr-only"> for {batch.batch_number}</span>
        </Button>
        <p id={statusId} role="status" aria-live="polite" className="text-xs">
          {error ? (
            <span role="alert" className="font-medium text-destructive">
              {error}
            </span>
          ) : download.isSuccess ? (
            <span className="text-muted-foreground">Downloaded.</span>
          ) : null}
        </p>
      </div>

      {masters.isPending ? (
        <p className="text-sm text-muted-foreground">Loading the Tally masters…</p>
      ) : masters.isError ? (
        <p role="alert" className="text-sm text-destructive">
          The list of Tally masters could not be loaded.{" "}
          <button
            type="button"
            className="font-medium underline underline-offset-4"
            onClick={() => void masters.refetch()}
          >
            Try again
          </button>
        </p>
      ) : (
        <MastersChecklist masters={masters.data} />
      )}
    </section>
  );
}
