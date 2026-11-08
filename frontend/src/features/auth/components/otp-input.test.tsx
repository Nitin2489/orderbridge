import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { EMPTY_OTP, OtpInput } from "./otp-input";

function Harness() {
  const [value, setValue] = useState<string[]>([...EMPTY_OTP]);
  return (
    <>
      <span id="otp-label">Code</span>
      <OtpInput value={value} onChange={setValue} labelledBy="otp-label" autoFocus />
      <output data-testid="value">{value.join("")}</output>
    </>
  );
}

function setup() {
  const user = userEvent.setup();
  render(<Harness />);
  const boxes = screen.getAllByRole("textbox");
  return { user, boxes, value: () => screen.getByTestId("value").textContent };
}

describe("OtpInput", () => {
  it("renders six labelled boxes in a labelled group and focuses the first", () => {
    const { boxes } = setup();

    expect(screen.getByRole("group", { name: "Code" })).toBeInTheDocument();
    expect(boxes).toHaveLength(6);
    expect(boxes[0]).toHaveAccessibleName("Digit 1 of 6");
    expect(boxes[0]).toHaveFocus();
  });

  it("advances to the next box as digits are typed", async () => {
    const { user, boxes, value } = setup();

    await user.keyboard("123");

    expect(value()).toBe("123");
    expect(boxes[3]).toHaveFocus();
  });

  it("ignores characters that are not digits", async () => {
    const { user, boxes, value } = setup();

    await user.keyboard("a1-b");

    expect(value()).toBe("1");
    expect(boxes[1]).toHaveFocus();
  });

  it("moves back and clears the previous digit on Backspace in an empty box", async () => {
    const { user, boxes, value } = setup();

    await user.keyboard("12");
    expect(boxes[2]).toHaveFocus();

    await user.keyboard("{Backspace}");

    expect(value()).toBe("1");
    expect(boxes[1]).toHaveFocus();
  });

  it("clears a filled box on Backspace without moving", async () => {
    const { user, boxes, value } = setup();

    await user.keyboard("12");
    await user.click(boxes[0]!);
    await user.keyboard("{Backspace}");

    expect(boxes[0]).toHaveValue("");
    expect(value()).toBe("2");
    expect(boxes[0]).toHaveFocus();
  });

  it("spreads a pasted code across all boxes", async () => {
    const { user, boxes, value } = setup();

    await user.paste("12 34 56");

    expect(value()).toBe("123456");
    expect(boxes[5]).toHaveFocus();
  });

  it("fills from the first box when a full code is pasted into a later box", async () => {
    const { user, boxes, value } = setup();

    await user.click(boxes[3]!);
    await user.paste("654321");

    expect(value()).toBe("654321");
  });

  it("moves between boxes with the arrow keys", async () => {
    const { user, boxes } = setup();

    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(boxes[2]).toHaveFocus();

    await user.keyboard("{ArrowLeft}");
    expect(boxes[1]).toHaveFocus();
  });
});
