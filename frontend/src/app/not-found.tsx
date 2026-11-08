import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <section
      aria-labelledby="not-found-heading"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <p className="text-sm font-medium text-muted-foreground">404</p>
      <h1 id="not-found-heading" className="text-2xl font-semibold tracking-tight">
        Page not found
      </h1>
      <p className="text-muted-foreground">
        The page you are looking for does not exist or has moved.
      </p>
      <Button asChild size="lg">
        <Link href="/">Back to overview</Link>
      </Button>
    </section>
  );
}
