"use client";

import { AlertCircleIcon, ArrowLeftIcon, InfoIcon, SearchXIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FormMessage } from "@/components/ui/form-message";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { Spinner } from "@/components/ui/spinner";
import { useSession } from "@/features/auth/session-provider";
import { formatIndiaDateTime } from "@/features/catalogue/format";
import { deskOrderPath } from "@/features/desk/paths";
import { newRequestId } from "@/features/exports/request-id";
import { orderPath } from "@/features/orders/paths";
import { isApiError } from "@/lib/api";

import {
  useCapture,
  useConfirmUnitForLines,
  useDiscardCapture,
  useReviewLine,
  useSubmitCapture,
} from "../api";
import { captureFailure, type CaptureFailure } from "../errors";
import { capturesPath, isCaptureId, type CaptureRole } from "../paths";
import type { CaptureDetail, CaptureLine, LineDecision } from "../types";
import { CaptureLineCard } from "./capture-line-card";
import { CaptureStatusBadge } from "./capture-status-badge";
import { HighlightedMessage } from "./highlighted-message";

const MIN_REASON = 3;
const MAX_REASON = 500;

function without<T>(record: Record<number, T>, key: number): Record<number, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

function BackLink({ role }: { role: CaptureRole }) {
  return (
    <Link
      href={capturesPath(role)}
      className="inline-flex w-fit items-center gap-1 rounded-sm text-sm font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <ArrowLeftIcon className="size-4" aria-hidden="true" />
      All captured messages
    </Link>
  );
}

/** Pending lines whose unit word is the same and still unconfirmed. */
function unitGroups(lines: CaptureLine[]): { word: string; lines: CaptureLine[] }[] {
  const groups = new Map<string, CaptureLine[]>();
  for (const line of lines) {
    if (
      line.resolution === "pending" &&
      !line.unit.confirmed &&
      line.unit_hint === "case_word" &&
      line.unit_text &&
      line.suggested_quantity
    ) {
      const word = line.unit_text.toLowerCase();
      groups.set(word, [...(groups.get(word) ?? []), line]);
    }
  }
  return [...groups.entries()]
    .filter(([, group]) => group.length > 1)
    .map(([word, group]) => ({ word, lines: group }));
}

function UnitShortcut({
  word,
  lines,
  disabled,
  onConfirm,
}: {
  word: string;
  lines: CaptureLine[];
  disabled: boolean;
  onConfirm: () => void;
}) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) {
    return null;
  }
  return (
    <Alert variant="info">
      <InfoIcon aria-hidden="true" />
      <AlertTitle>
        {lines.length} lines say “{word}”. Does the sender mean cases in each of them?
      </AlertTitle>
      <AlertDescription>
        <p>
          Lines {lines.map((line) => line.line_number).join(", ")}: the quantities stay as written
          ({lines.map((line) => line.suggested_quantity).join(", ")}). Products are still chosen
          line by line.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button type="button" className="h-11" disabled={disabled} onClick={onConfirm}>
            Yes, “{word}” means cases
          </Button>
          <Button
            type="button"
            variant="ghost"
            className="h-11 text-foreground"
            disabled={disabled}
            onClick={() => setDismissed(true)}
          >
            I will confirm each line
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

function ReviewWorkspace({
  capture,
  role,
  sessionUserId,
}: {
  capture: CaptureDetail;
  role: CaptureRole;
  sessionUserId: string;
}) {
  const router = useRouter();
  const review = useReviewLine(capture.id);
  const units = useConfirmUnitForLines(capture.id);
  const discard = useDiscardCapture(capture.id);
  const submit = useSubmitCapture(capture.id);
  const busy = review.isPending || units.isPending || discard.isPending || submit.isPending;

  const [lineErrors, setLineErrors] = useState<Record<number, string>>({});
  const [aliasConflicts, setAliasConflicts] = useState<Set<number>>(new Set());
  const [issues, setIssues] = useState<Record<number, string>>({});
  const [banner, setBanner] = useState<CaptureFailure | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [discardReason, setDiscardReason] = useState("");
  const [discardError, setDiscardError] = useState<string | null>(null);
  // Kept for a manual retry after an uncertain submission; replaced after
  // any definite outcome.
  const submitKey = useRef<string | null>(null);
  const [retrySubmit, setRetrySubmit] = useState(false);

  const editable = capture.status === "needs_review" || capture.status === "ready";
  const orderHref = (id: string) => (role === "order_desk" ? deskOrderPath(id) : orderPath(id));

  const decide = (line: CaptureLine, decision: LineDecision) => {
    setBanner(null);
    review.mutate(
      {
        lineNumber: line.line_number,
        decision,
        expectedVersion: capture.version,
        sessionUserId,
      },
      {
        onSuccess: () => {
          setLineErrors((current) => without(current, line.line_number));
          setIssues((current) => without(current, line.line_number));
          if (decision.action !== "include") {
            return;
          }
          setAliasConflicts((current) => {
            const next = new Set(current);
            next.delete(line.line_number);
            return next;
          });
        },
        onError: (error) => {
          const failure = captureFailure(error);
          if (failure.kind === "alias_conflict") {
            setAliasConflicts((current) => new Set(current).add(line.line_number));
          }
          if (["stale", "state", "uncertain", "not_found"].includes(failure.kind)) {
            setBanner(failure);
          } else {
            setLineErrors((current) => ({ ...current, [line.line_number]: failure.message }));
          }
        },
      },
    );
  };

  const confirmGroup = (lines: CaptureLine[]) => {
    setBanner(null);
    units.mutate(
      {
        lines: lines.map((line) => ({
          lineNumber: line.line_number,
          quantity: line.suggested_quantity ?? 0,
        })),
        unit: "case",
        expectedVersion: capture.version,
        sessionUserId,
      },
      { onError: (error) => setBanner(captureFailure(error)) },
    );
  };

  const submitCapture = () => {
    setBanner(null);
    submitKey.current ??= newRequestId();
    submit.mutate(
      { clientRequestId: submitKey.current, expectedVersion: capture.version, sessionUserId },
      {
        onSuccess: (result) => {
          submitKey.current = null;
          setRetrySubmit(false);
          router.push(orderHref(result.order.id));
        },
        onError: (error) => {
          const failure = captureFailure(error);
          const uncertain = failure.kind === "uncertain";
          if (!uncertain) {
            submitKey.current = null;
          }
          setRetrySubmit(uncertain);
          if (failure.kind === "lines_invalid") {
            setIssues(
              Object.fromEntries(failure.issues.map((issue) => [issue.line_number, issue.message])),
            );
          }
          setBanner(failure);
        },
      },
    );
  };

  const discardCapture = () => {
    const trimmed = discardReason.trim();
    if (trimmed.length < MIN_REASON || trimmed.length > MAX_REASON) {
      setDiscardError(`Enter a reason of ${MIN_REASON} to ${MAX_REASON} characters.`);
      return;
    }
    setDiscardError(null);
    discard.mutate(
      { reason: trimmed, expectedVersion: capture.version, sessionUserId },
      {
        onSuccess: () => setDiscarding(false),
        onError: (error) => setBanner(captureFailure(error)),
      },
    );
  };

  const groups = editable ? unitGroups(capture.lines) : [];
  const created = formatIndiaDateTime(capture.created_at) ?? capture.created_at;
  const remaining = capture.unresolved_lines.length;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <BackLink role={role} />

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Review message</h1>
          <CaptureStatusBadge status={capture.status} />
        </div>
        <p className="text-sm text-muted-foreground">
          {capture.retailer.business_name} · Received {created}
        </p>
        <p className="text-sm text-muted-foreground">
          {capture.interpretation.is_ai_model
            ? capture.interpretation.description
            : "Read with rule-based interpretation (word rules and product matching, not an AI model). Every line needs your decision; nothing is ordered until you create the order and the order desk confirms it."}
        </p>
      </div>

      <section aria-labelledby="message-heading" className="flex flex-col gap-2">
        <h2 id="message-heading" className="text-base font-semibold">
          Message, exactly as received
        </h2>
        <HighlightedMessage capture={capture} />
        {capture.ignored.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            Not part of any line:{" "}
            {capture.ignored
              .map((span) => `“${span.text}” (${span.reason === "greeting" ? "greeting" : "no words"})`)
              .join(", ")}
          </p>
        ) : null}
      </section>

      <div aria-live="polite" className="flex flex-col gap-3">
        {banner ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>{banner.message}</AlertDescription>
          </Alert>
        ) : null}
        {capture.status === "submitted" && capture.order ? (
          <Alert variant="info" role="status">
            <InfoIcon aria-hidden="true" />
            <AlertTitle>Order {capture.order.order_number} was created from this message</AlertTitle>
            <AlertDescription>
              <p>It is waiting for the order desk to confirm it; this capture can no longer change.</p>
              <Button asChild variant="outline" className="mt-3 h-11 text-foreground">
                <Link href={orderHref(capture.order.id)}>View the order</Link>
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {capture.status === "discarded" ? (
          <Alert role="status">
            <InfoIcon aria-hidden="true" />
            <AlertTitle>This message was discarded</AlertTitle>
            <AlertDescription>Reason: {capture.discard_reason}</AlertDescription>
          </Alert>
        ) : null}
      </div>

      {groups.map((group) => (
        <UnitShortcut
          key={group.word}
          word={group.word}
          lines={group.lines}
          disabled={busy}
          onConfirm={() => confirmGroup(group.lines)}
        />
      ))}

      <section aria-labelledby="lines-heading" className="flex flex-col gap-4">
        <h2 id="lines-heading" className="text-base font-semibold">
          Lines ({capture.lines.length})
        </h2>
        {capture.lines.map((line) => (
          <CaptureLineCard
            key={`${line.line_number}-${line.resolution}`}
            line={line}
            retailerName={capture.retailer.business_name}
            editable={editable}
            busy={busy}
            canSaveAlias={role === "order_desk"}
            issue={issues[line.line_number] ?? null}
            error={lineErrors[line.line_number] ?? null}
            aliasConflict={aliasConflicts.has(line.line_number)}
            onDecide={(decision) => decide(line, decision)}
          />
        ))}
      </section>

      {editable ? (
        <section
          aria-labelledby="finish-heading"
          className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs"
        >
          <h2 id="finish-heading" className="text-base font-semibold">
            Create the order
          </h2>
          <p className="text-sm text-muted-foreground">
            {capture.can_submit
              ? "Every line has a decision. OrderBridge prices the included lines with the retailer's current prices and schemes, and the order goes to the order desk as pending review. No stock or credit is used until the desk confirms it."
              : remaining > 0
                ? `${remaining} ${remaining === 1 ? "line needs" : "lines need"} a decision: line ${capture.unresolved_lines.join(", ")}.`
                : "Include at least one line with a confirmed quantity, or discard the message."}
          </p>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Button
              type="button"
              size="lg"
              disabled={busy || !capture.can_submit}
              onClick={submitCapture}
            >
              {submit.isPending ? (
                <>
                  <Spinner />
                  Creating order…
                </>
              ) : retrySubmit ? (
                "Retry creating order"
              ) : (
                "Create order for review"
              )}
            </Button>
            {!discarding ? (
              <Button
                type="button"
                variant="ghost"
                size="lg"
                disabled={busy}
                onClick={() => setDiscarding(true)}
              >
                Discard message…
              </Button>
            ) : null}
          </div>
          {discarding ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="discard-reason">Reason for discarding</Label>
              <textarea
                id="discard-reason"
                rows={2}
                value={discardReason}
                disabled={busy}
                maxLength={MAX_REASON * 2}
                aria-invalid={discardError ? true : undefined}
                aria-describedby={discardError ? "discard-reason-error" : undefined}
                onChange={(event) => setDiscardReason(event.target.value)}
                className="w-full rounded-md border border-input bg-card px-3 py-2 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive"
              />
              <FormMessage id="discard-reason-error">{discardError}</FormMessage>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="destructive"
                  className="h-11"
                  disabled={busy}
                  onClick={discardCapture}
                >
                  Discard message
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="h-11"
                  disabled={busy}
                  onClick={() => setDiscarding(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function ReviewLoader({ captureId, role }: { captureId: string; role: CaptureRole }) {
  const { user } = useSession();
  const capture = useCapture(captureId);

  const notFound =
    !isCaptureId(captureId) ||
    (capture.isError && isApiError(capture.error) && [404, 422].includes(capture.error.status));
  if (notFound) {
    return (
      <section className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center">
        <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
          <SearchXIcon className="size-6" aria-hidden="true" />
        </span>
        <h1 className="text-2xl font-semibold tracking-tight">Message not found</h1>
        <Button asChild size="lg">
          <Link href={capturesPath(role)}>Back to captured messages</Link>
        </Button>
      </section>
    );
  }
  if (capture.isPending || !user) {
    return <LoadingState label="Loading message…" />;
  }
  if (capture.isError) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <BackLink role={role} />
        <Alert variant="destructive" role="alert">
          <AlertCircleIcon aria-hidden="true" />
          <AlertTitle>
            {isApiError(capture.error) && capture.error.status === 0
              ? capture.error.message
              : "This message could not be loaded."}
          </AlertTitle>
          <AlertDescription>
            <Button
              type="button"
              variant="outline"
              className="mt-3 h-11 text-foreground"
              onClick={() => void capture.refetch()}
            >
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  return <ReviewWorkspace capture={capture.data} role={role} sessionUserId={user.id} />;
}

/** Review one captured message. Keyed by the signed-in user. */
export function CaptureReviewScreen({ captureId, role }: { captureId: string; role: CaptureRole }) {
  const { user } = useSession();
  if (!user) {
    return <LoadingState label="Loading…" />;
  }
  return <ReviewLoader key={user.id} captureId={captureId} role={role} />;
}
