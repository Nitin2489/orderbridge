"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { workspaceFor } from "../roles";
import { formatPhoneNumber } from "../schemas";
import { useSession } from "../session-provider";
import { SignOutButton } from "./sign-out-button";

const NEXT_UP: Record<string, string> = {
  retailer: "Your catalogue, cart and orders will appear here.",
  order_desk: "The order review queue and Tally export will appear here.",
};

/**
 * Workspace landing for the signed-in user. Everything shown comes from the
 * backend session; RoleGate has already checked the role.
 */
export function WorkspaceHome() {
  const { user } = useSession();
  if (!user) {
    return null;
  }

  const workspace = workspaceFor(user.role);
  const details: { label: string; value: string }[] = [
    { label: "Name", value: user.full_name },
    { label: "Mobile", value: formatPhoneNumber(user.phone_number) },
    { label: "Role", value: workspace?.label ?? user.role },
  ];
  if (user.retailer) {
    details.push(
      { label: "Business", value: user.retailer.business_name },
      { label: "Address", value: user.retailer.address },
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-primary">{workspace?.label} workspace</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-balance sm:text-3xl">
            {user.retailer?.business_name ?? `Welcome, ${user.full_name}`}
          </h1>
          <p className="mt-2 text-muted-foreground">{NEXT_UP[user.role]}</p>
        </div>
        <SignOutButton />
      </div>

      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle>Your account</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[8rem_1fr]">
            {details.map(({ label, value }) => (
              <div key={label} className="flex flex-col gap-0.5 sm:contents">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="font-medium break-words">{value}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
