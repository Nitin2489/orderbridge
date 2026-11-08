"use client";

import { CircleAlertIcon, CircleCheckIcon, CircleSlashIcon } from "lucide-react";
import { useId, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormMessage } from "@/components/ui/form-message";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatCount } from "@/features/catalogue/format";
import { cn } from "@/lib/utils";

import type { Candidate, CaptureLine, LineDecision } from "../types";
import { MatchScore } from "./match-score";

const MIN_REASON = 3;
const MAX_REASON = 500;
const MAX_QUANTITY = 1_000_000;

export interface CaptureLineCardProps {
  line: CaptureLine;
  retailerName: string;
  /** False once the capture is submitted or discarded. */
  editable: boolean;
  /** Another change is in flight. */
  busy: boolean;
  /** Only the order desk may save aliases. */
  canSaveAlias: boolean;
  /** Why the line cannot be ordered (from a refused submission). */
  issue?: string | null;
  /** The last error for this line's own decisions. */
  error?: string | null;
  /** The phrase is already saved for this retailer as another product. */
  aliasConflict?: boolean;
  onDecide: (decision: LineDecision) => void;
}

function unitWords(unit: "case" | "piece", quantity: number): string {
  return `${formatCount(quantity)} ${unit === "case" ? (quantity === 1 ? "case" : "cases") : quantity === 1 ? "piece" : "pieces"}`;
}

function unitHintText(line: CaptureLine): string {
  const said = line.quantity_text || line.unit_text
    ? `The message says "${[line.quantity_text, line.unit_text].filter(Boolean).join(" ")}". `
    : "";
  switch (line.unit_hint) {
    case "case_word":
      return `${said}"${line.unit_text}" usually means a case, but confirm it: it is never assumed.`;
    case "piece_word":
      return `${said}Orders are placed in whole cases: enter cases, or a number of pieces that makes whole cases.`;
    default:
      return `${said}No unit was given. Confirm whether the quantity is in cases or pieces.`;
  }
}

function ProductOption({
  candidate,
  name,
  checked,
  suggested,
  disabled,
  onChange,
}: {
  candidate: Candidate;
  name: string;
  checked: boolean;
  suggested: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  return (
    <label
      className={cn(
        "flex min-h-14 cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50",
        checked ? "border-primary bg-accent/40" : "hover:bg-secondary/50",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <input
        type="radio"
        name={name}
        value={candidate.product_id}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        className="mt-1 size-4 accent-[var(--primary)]"
      />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{candidate.name}</span>
          {suggested ? <Badge variant="default">Suggested</Badge> : null}
          {candidate.weak_match && !suggested ? (
            <Badge variant="outline">Weak match</Badge>
          ) : null}
          {candidate.stock_cases === 0 ? <Badge variant="destructive">Out of stock</Badge> : null}
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          <span className="sr-only">SKU </span>
          {candidate.sku}
        </span>
        <MatchScore candidate={candidate} />
        {candidate.stock_cases !== undefined && candidate.stock_cases !== null && candidate.stock_cases > 0 ? (
          <span className="text-xs text-muted-foreground">
            {formatCount(candidate.stock_cases)} {candidate.stock_cases === 1 ? "case" : "cases"} in stock
          </span>
        ) : null}
        {candidate.reasons.length > 0 ? (
          <span className="text-xs text-muted-foreground">{candidate.reasons.join(" ")}</span>
        ) : null}
      </span>
    </label>
  );
}

/** One proposed line and the reviewer's decision for it. */
export function CaptureLineCard({
  line,
  retailerName,
  editable,
  busy,
  canSaveAlias,
  issue,
  error,
  aliasConflict = false,
  onDecide,
}: CaptureLineCardProps) {
  const id = useId();
  const proposal = line.match.proposal;
  const options = [proposal, ...line.match.alternatives].filter(
    (candidate, index, all): candidate is Candidate =>
      candidate !== null && all.findIndex((c) => c?.product_id === candidate.product_id) === index,
  );

  const [productId, setProductId] = useState<string | null>(
    line.selected_product?.id ?? proposal?.product_id ?? null,
  );
  const [quantity, setQuantity] = useState(
    line.suggested_quantity ? String(line.suggested_quantity) : "",
  );
  const [unit, setUnit] = useState<"case" | "piece" | null>(null);
  const [unitError, setUnitError] = useState<string | null>(null);
  const [excluding, setExcluding] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [remember, setRemember] = useState(false);
  const [replace, setReplace] = useState(false);

  const disabled = !editable || busy;
  const corrected = productId !== null && productId !== (proposal?.product_id ?? null);
  const aliasOffered = canSaveAlias && corrected && line.product_text !== null;
  const chosen = options.find((option) => option.product_id === productId) ?? null;
  const includedOutOfStock =
    line.resolution === "included" &&
    options.some(
      (option) => option.product_id === line.selected_product?.id && option.stock_cases === 0,
    );

  const confirmUnit = () => {
    const value = Number(quantity);
    if (!unit) {
      setUnitError("Choose cases or pieces.");
      return;
    }
    if (!Number.isInteger(value) || value < 1 || value > MAX_QUANTITY) {
      setUnitError("Enter a whole number of 1 or more.");
      return;
    }
    setUnitError(null);
    onDecide({ action: "confirm_unit", unit, quantity: value });
  };

  const exclude = () => {
    const trimmed = reason.trim();
    if (trimmed.length < MIN_REASON || trimmed.length > MAX_REASON) {
      setReasonError(`Enter a reason of ${MIN_REASON} to ${MAX_REASON} characters.`);
      return;
    }
    setReasonError(null);
    onDecide({ action: "exclude", reason: trimmed });
  };

  const include = () => {
    if (!productId) {
      return;
    }
    onDecide({
      action: "include",
      product_id: productId,
      ...(aliasOffered && remember ? { remember_alias: true } : {}),
      ...(aliasOffered && remember && replace ? { replace_existing_alias: true } : {}),
    });
  };

  const headingId = `${id}-heading`;
  const resolved = line.resolution !== "pending";

  return (
    <article
      aria-labelledby={headingId}
      className={cn(
        "flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs",
        issue && "border-destructive/50",
        line.resolution === "included" && "border-emerald-500/40",
      )}
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id={headingId} className="text-sm font-medium text-muted-foreground">
            Line {line.line_number}
          </h3>
          <p className="mt-1 text-lg font-semibold break-words">
            <span className="sr-only">Original words: </span>“{line.original_text}”
          </p>
        </div>
        {line.resolution === "included" ? (
          <Badge variant="success">
            <CircleCheckIcon aria-hidden="true" />
            Included
          </Badge>
        ) : line.resolution === "excluded" ? (
          <Badge variant="outline">
            <CircleSlashIcon aria-hidden="true" />
            Excluded
          </Badge>
        ) : (
          <Badge variant="secondary">Needs a decision</Badge>
        )}
      </header>

      {line.notes.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-muted-foreground">
          {line.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {issue ? (
        <p role="alert" className="flex items-start gap-2 text-sm font-medium text-destructive">
          <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {issue}
        </p>
      ) : null}

      {line.resolution === "included" && line.selected_product ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Product</dt>
          <dd>
            {line.selected_product.name}{" "}
            <span className="font-mono text-xs text-muted-foreground">{line.selected_product.sku}</span>
          </dd>
          <dt className="text-muted-foreground">Quantity</dt>
          <dd>
            {line.unit.quantity_cases !== null ? unitWords("case", line.unit.quantity_cases) : null}
            {line.unit.unit === "piece" && line.unit.quantity !== null
              ? ` (${unitWords("piece", line.unit.quantity)} entered)`
              : null}
          </dd>
          {line.remember_alias ? (
            <>
              <dt className="text-muted-foreground">Alias</dt>
              <dd>
                “{line.product_text}” will be saved for {retailerName} when the order is created.
              </dd>
            </>
          ) : null}
        </dl>
      ) : null}

      {includedOutOfStock ? (
        <p className="flex items-start gap-2 text-sm font-medium text-amber-700 dark:text-amber-300">
          <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          This product is out of stock, so the order cannot be created with this line. Choose
          another product or exclude the line with a reason.
        </p>
      ) : null}

      {line.resolution === "excluded" ? (
        <p className="text-sm">
          <span className="text-muted-foreground">Reason: </span>
          {line.exclusion_reason}
        </p>
      ) : null}

      {!resolved ? (
        <>
          <fieldset className="flex flex-col gap-2" disabled={disabled}>
            <legend className="mb-1 text-sm font-semibold">Product</legend>
            {line.match.reasons.length > 0 ? (
              <p className="text-sm text-muted-foreground">{line.match.reasons.join(" ")}</p>
            ) : null}
            {options.length === 0 ? (
              <p className="text-sm text-muted-foreground">No products to choose from.</p>
            ) : (
              options.map((candidate) => (
                <ProductOption
                  key={candidate.product_id}
                  candidate={candidate}
                  name={`${id}-product`}
                  checked={productId === candidate.product_id}
                  suggested={proposal?.product_id === candidate.product_id}
                  disabled={disabled}
                  onChange={() => setProductId(candidate.product_id)}
                />
              ))
            )}
          </fieldset>

          <section aria-labelledby={`${id}-quantity`} className="flex flex-col gap-2">
            <h4 id={`${id}-quantity`} className="text-sm font-semibold">
              Quantity
            </h4>
            {line.unit.confirmed && line.unit.unit && line.unit.quantity !== null ? (
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className="inline-flex items-center gap-1.5 font-medium">
                  <CircleCheckIcon aria-hidden="true" className="size-4 text-emerald-600" />
                  {unitWords(line.unit.unit, line.unit.quantity)}
                </span>
                {editable ? (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-11"
                    disabled={disabled}
                    onClick={() => onDecide({ action: "clear_unit" })}
                  >
                    Change quantity
                  </Button>
                ) : null}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                <p className="text-sm text-muted-foreground">{unitHintText(line)}</p>
                <div className="flex flex-wrap items-end gap-3">
                  <div className="flex flex-col gap-1">
                    <Label htmlFor={`${id}-qty`}>Quantity</Label>
                    <Input
                      id={`${id}-qty`}
                      inputMode="numeric"
                      value={quantity}
                      disabled={disabled}
                      aria-invalid={unitError ? true : undefined}
                      aria-describedby={unitError ? `${id}-unit-error` : undefined}
                      onChange={(event) => setQuantity(event.target.value.replace(/\D/g, "").slice(0, 7))}
                      className="w-28"
                    />
                  </div>
                  <fieldset className="flex gap-2" disabled={disabled}>
                    <legend className="sr-only">Unit</legend>
                    {(["case", "piece"] as const).map((value) => (
                      <label
                        key={value}
                        className={cn(
                          "inline-flex h-11 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm font-medium has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50",
                          unit === value ? "border-primary bg-accent/40" : "hover:bg-secondary/50",
                        )}
                      >
                        <input
                          type="radio"
                          name={`${id}-unit`}
                          value={value}
                          checked={unit === value}
                          onChange={() => setUnit(value)}
                          className="size-4 accent-[var(--primary)]"
                        />
                        {value === "case" ? "Cases" : "Pieces"}
                      </label>
                    ))}
                  </fieldset>
                  <Button type="button" variant="outline" className="h-11" disabled={disabled} onClick={confirmUnit}>
                    Confirm quantity
                  </Button>
                </div>
                <FormMessage id={`${id}-unit-error`}>{unitError}</FormMessage>
              </div>
            )}
          </section>

          {aliasOffered ? (
            <div className="flex flex-col gap-2 rounded-lg bg-secondary/50 p-3 text-sm">
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  checked={remember}
                  disabled={disabled}
                  onChange={(event) => setRemember(event.target.checked)}
                  className="mt-0.5 size-4 accent-[var(--primary)]"
                />
                <span>
                  Remember “{line.product_text}” as {chosen?.name} for {retailerName}
                </span>
              </label>
              {aliasConflict && remember ? (
                <label className="flex cursor-pointer items-start gap-2 pl-6">
                  <input
                    type="checkbox"
                    checked={replace}
                    disabled={disabled}
                    onChange={(event) => setReplace(event.target.checked)}
                    className="mt-0.5 size-4 accent-[var(--primary)]"
                  />
                  <span>
                    Replace the alias already saved for this phrase (the old one is kept in the
                    history).
                  </span>
                </label>
              ) : null}
            </div>
          ) : null}

          {excluding ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor={`${id}-reason`}>Reason for excluding this line</Label>
              <textarea
                id={`${id}-reason`}
                rows={2}
                value={reason}
                disabled={disabled}
                maxLength={MAX_REASON * 2}
                aria-invalid={reasonError ? true : undefined}
                aria-describedby={reasonError ? `${id}-reason-error` : undefined}
                onChange={(event) => setReason(event.target.value)}
                className="w-full rounded-md border border-input bg-card px-3 py-2 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive"
              />
              <FormMessage id={`${id}-reason-error`}>{reasonError}</FormMessage>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="destructive" className="h-11" disabled={disabled} onClick={exclude}>
                  Exclude line
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="h-11"
                  disabled={disabled}
                  onClick={() => setExcluding(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : editable ? (
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button
                type="button"
                className="h-11"
                disabled={disabled || !productId || !line.unit.confirmed}
                onClick={include}
              >
                Include line
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-11"
                disabled={disabled}
                onClick={() => setExcluding(true)}
              >
                Exclude…
              </Button>
              {!line.unit.confirmed ? (
                <p className="self-center text-xs text-muted-foreground">
                  Confirm the quantity before including the line.
                </p>
              ) : null}
            </div>
          ) : null}
        </>
      ) : editable ? (
        <Button
          type="button"
          variant="ghost"
          className="h-11 w-fit"
          disabled={disabled}
          onClick={() => onDecide({ action: "reset" })}
        >
          Change decision
        </Button>
      ) : null}

      {error ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      ) : null}
    </article>
  );
}
