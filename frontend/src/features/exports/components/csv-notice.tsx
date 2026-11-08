/** Explains what the file is, wherever an export is offered. */
export function CsvNotice() {
  return (
    <p className="text-sm text-muted-foreground">
      The CSV is a generic, line-level sales register for importing into Tally. It is not a
      verified native Tally import: ledgers, GST and voucher types must be mapped in Tally.
      Amounts exclude GST.
    </p>
  );
}
