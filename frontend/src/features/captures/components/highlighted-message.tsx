import type { ReactNode } from "react";

import type { CaptureDetail } from "../types";

/**
 * The message exactly as received, with each order line marked by its
 * offsets. Nothing in the text is changed; text outside every line
 * (greetings, punctuation) is shown as it is.
 */
export function HighlightedMessage({ capture }: { capture: CaptureDetail }) {
  const message = capture.original_message;
  const parts: ReactNode[] = [];
  let position = 0;
  for (const line of [...capture.lines].sort((a, b) => a.start - b.start)) {
    if (line.start > position) {
      parts.push(message.slice(position, line.start));
    }
    parts.push(
      <mark
        key={line.line_number}
        className="rounded bg-accent px-0.5 text-accent-foreground [box-decoration-break:clone]"
      >
        <sup className="mr-0.5 font-semibold" aria-hidden="true">
          {line.line_number}
        </sup>
        <span className="sr-only">Line {line.line_number}: </span>
        {message.slice(line.start, line.end)}
      </mark>,
    );
    position = line.end;
  }
  if (position < message.length) {
    parts.push(message.slice(position));
  }
  return (
    <blockquote className="rounded-lg border bg-card p-4 text-base leading-relaxed whitespace-pre-wrap break-words">
      {parts}
    </blockquote>
  );
}
