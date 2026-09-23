import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CopyValue } from "./CopyValue";

const FINGERPRINT = "06aa652c638d12400f5e8210778b0d0842dab3c70cfbd59bbb84a67879dab7a5";

function withClipboard(writeText: () => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
}

describe("CopyValue", () => {
  afterEach(() => Reflect.deleteProperty(navigator, "clipboard"));

  it("copies the whole value, which is the part nobody types by hand", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    withClipboard(writeText);

    render(<CopyValue value={FINGERPRINT} label="the snapshot fingerprint" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy the snapshot fingerprint" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("copied to the clipboard"));
    expect(writeText).toHaveBeenCalledWith(FINGERPRINT);
  });

  it("renders nothing where the clipboard is unavailable", () => {
    render(<CopyValue value={FINGERPRINT} label="the snapshot fingerprint" />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("says a blocked copy was blocked", async () => {
    withClipboard(vi.fn().mockRejectedValue(new Error("denied")));

    render(<CopyValue value={FINGERPRINT} label="the snapshot fingerprint" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy the snapshot fingerprint" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Copying the snapshot fingerprint was blocked" }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/copied to the clipboard/)).not.toBeInTheDocument();
  });
});
