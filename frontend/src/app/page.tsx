import { ClipboardCheck, MessageSquareText, Store } from "lucide-react";

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Landing placeholder. It describes what OrderBridge is for; it shows no
 * operational data and offers no actions until the real screens exist.
 */
const capabilities = [
  {
    icon: Store,
    title: "Retailer ordering",
    description:
      "Retailers see their own prices, current schemes and stock, and place orders in a minute from a phone.",
  },
  {
    icon: MessageSquareText,
    title: "Order capture from messages",
    description:
      "Informal orders sent as messages become structured drafts that a person checks before anything is placed.",
  },
  {
    icon: ClipboardCheck,
    title: "Order desk review",
    description:
      "The desk confirms orders against live stock, schemes and credit, then exports confirmed orders for Tally.",
  },
] as const;

export default function HomePage() {
  return (
    <div className="flex flex-col gap-12">
      <section aria-labelledby="welcome-heading" className="max-w-2xl">
        <h1
          id="welcome-heading"
          className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl"
        >
          Every order validated, reviewable and accurately priced
        </h1>
        <p className="mt-4 text-base text-muted-foreground sm:text-lg">
          OrderBridge connects retailers, salesmen and the order desk of an FMCG
          distributor, from the first message to the confirmed order.
        </p>
      </section>

      <section aria-labelledby="capabilities-heading">
        <h2 id="capabilities-heading" className="sr-only">
          What OrderBridge does
        </h2>
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {capabilities.map(({ icon: Icon, title, description }) => (
            <li key={title}>
              <Card className="h-full">
                <CardHeader className="gap-3">
                  <span className="inline-flex size-10 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                    <Icon className="size-5" aria-hidden="true" />
                  </span>
                  <CardTitle>{title}</CardTitle>
                  <CardDescription>{description}</CardDescription>
                </CardHeader>
              </Card>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
