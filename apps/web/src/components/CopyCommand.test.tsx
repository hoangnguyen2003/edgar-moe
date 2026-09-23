import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CopyCommand } from "./CopyCommand";

const COMMAND = "uv run python scripts/smoke_deployment.py \\\n  https://edgar-moe.vercel.app";

function withClipboard(writeText: () => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
}

describe("CopyCommand", () => {
  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
    vi.useRealTimers();
  });

  it("shows the command whether or not it can be copied", () => {
    render(<CopyCommand command={COMMAND} label="command" />);

    expect(screen.getByText(/smoke_deployment\.py/)).toBeInTheDocument();
    // No clipboard in this context, so no button that would not work.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("copies the command exactly and confirms it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    withClipboard(writeText);

    render(<CopyCommand command={COMMAND} label="command" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy command" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument());
    // The backslash continuations have to survive, or the pasted command breaks.
    expect(writeText).toHaveBeenCalledWith(COMMAND);
    expect(screen.getByRole("status")).toHaveTextContent("command copied to the clipboard");
  });

  it("says so when copying is blocked instead of claiming success", async () => {
    withClipboard(vi.fn().mockRejectedValue(new Error("denied")));

    render(<CopyCommand command={COMMAND} label="command" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy command" }));

    await waitFor(() =>
      expect(screen.getByText(/Copying was blocked; select the text instead\./)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Copied" })).not.toBeInTheDocument();
  });
});
