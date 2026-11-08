import type { Metadata } from "next";
import { MessageSquareTextIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CATALOGUE_PATH } from "@/features/cart/paths";

export const metadata: Metadata = {
  title: "Order capture",
  robots: { index: false },
};

export default function RetailerCapturePage() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Order capture from messages
        </h1>
        <p className="text-sm text-muted-foreground">
          Rule-based order capture from Kirana WhatsApp messages
        </p>
      </div>

      <Card>
        <CardHeader className="gap-3">
          <span className="inline-flex size-10 items-center justify-center rounded-lg bg-accent text-accent-foreground">
            <MessageSquareTextIcon className="size-5" aria-hidden="true" />
          </span>
          <CardTitle>Fast message-to-order capture</CardTitle>
          <CardDescription>
            OrderBridge captures text orders sent as informal messages and extracts product names,
            quantities and units with deterministic rule-based matching. Every order line is reviewed
            and confirmed before placing.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            This module integrates with the OrderBridge capture engine. Interactive message intake
            and unit review will be enabled in Stage 6.
          </p>
          <Button asChild variant="outline" className="w-fit">
            <Link href={CATALOGUE_PATH}>Browse catalogue instead</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
