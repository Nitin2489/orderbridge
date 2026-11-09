import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Breadcrumbs } from "./breadcrumbs";

describe("Breadcrumbs", () => {
  it("gives links a 44 px tap area and marks the current page", () => {
    render(
      <Breadcrumbs
        items={[
          { label: "Order queue", href: "/desk" },
          { label: "OB-2026-000001", current: true },
        ]}
      />,
    );

    const link = screen.getByRole("link", { name: "Order queue" });
    expect(link).toHaveAttribute("href", "/desk");
    expect(link).toHaveClass("tap-target");
    expect(screen.getByText("OB-2026-000001")).toHaveAttribute("aria-current", "page");
  });
});
