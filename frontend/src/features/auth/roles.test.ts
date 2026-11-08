import { describe, expect, it } from "vitest";

import { isUserRole, workspaceFor } from "./roles";

describe("workspaceFor", () => {
  it("sends retailers to /retailer and the order desk to /desk", () => {
    expect(workspaceFor("retailer")?.path).toBe("/retailer");
    expect(workspaceFor("order_desk")?.path).toBe("/desk");
  });

  it.each(["salesman", "admin", "", "Retailer", "__proto__", "toString"])(
    "has no workspace for unknown role %j",
    (role) => {
      expect(isUserRole(role)).toBe(false);
      expect(workspaceFor(role)).toBeNull();
    },
  );
});
