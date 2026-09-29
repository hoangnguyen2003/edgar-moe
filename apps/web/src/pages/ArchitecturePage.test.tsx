import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../lib/router";
import { ArchitecturePage } from "./ArchitecturePage";

const governance = {
  schema_version: 2,
  published_snapshot: {
    path: "data/demo/snapshot.json",
    sha256: "a".repeat(64),
    data_mode: "synthetic_fixture",
    as_of: "2026-07-31",
    selection_hash: null,
    locked_test_hash: null,
    research_only: true,
  },
  public_data: { raw_sources_public: false, current_output_mode: "synthetic_fixture", historical_v1_served_by_application: false, prospective_outputs_served_by_application: false, redistribution_status: "historical_v1_review_required" },
  controls: [
    { key: "published_snapshot_identity", status: "enforced", owner: "repository", summary: "" },
    { key: "pre_entry_forecasts", status: "enforced", owner: "repository", summary: "" },
    { key: "append_only_outcomes", status: "enforced", owner: "repository", summary: "" },
    { key: "provider_operations", status: "pending_operator_evidence", owner: "operator", summary: "" },
  ],
  forward_status: { public_visibility: "withheld_review", message: "Prospective forecasts and outcomes are withheld from the public application pending source-rights review. The registry remains private." },
};

function renderPage(response: () => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(response));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><RouterProvider><ArchitecturePage /></RouterProvider></QueryClientProvider>);
}

describe("Architecture page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("explains the system in five ordered lanes with the key decisions", () => {
    renderPage(() => new Promise<Response>(() => {}));

    const lanes = screen.getByRole("region", { name: "How the system fits together" });
    const titles = within(lanes).getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(titles).toEqual(["Historical research study", "Public site", "Live tracking", "Evidence copilot", "Delivery and checks"]);
    const copilot = screen.getByRole("region", { name: "Evidence copilot" });
    expect(within(copilot).getByText(/read-only evidence tools/i)).toBeInTheDocument();
    expect(within(copilot).getByText(/Human review/i)).toBeInTheDocument();
    const delivery = screen.getByRole("region", { name: "Delivery and checks" });
    expect(within(delivery).getAllByRole("listitem").map((item) => item.querySelector("strong")?.textContent)).toEqual([
      "Pull request",
      "Automated checks",
      "Deploy",
      "Post-deploy check",
    ]);
    const decisions = screen.getByRole("region", { name: "Key design decisions" });
    expect(within(decisions).getByRole("link", { name: /Make the database itself refuse edits/ })).toHaveAttribute(
      "href",
      "https://github.com/hoangnguyen2003/edgar-moe/blob/main/docs/adr/0016-database-append-only-triggers.md",
    );
    expect(within(decisions).getByRole("link", { name: /Keep AI review profiles bounded and read-only/ })).toHaveAttribute(
      "href",
      "https://github.com/hoangnguyen2003/edgar-moe/blob/main/docs/adr/0022-copilot-review-profiles.md",
    );
    expect(within(decisions).getByRole("link", { name: /Separate human review records from diagnostic evidence/ })).toHaveAttribute(
      "href",
      "https://github.com/hoangnguyen2003/edgar-moe/blob/main/docs/adr/0028-forward-history-review-attestations.md",
    );
    // scripts/validate_adr_catalog.py owns the exact count; it must match the
    // records on disk, so asserting it here as well only duplicates that check.
    expect(within(decisions).getByText(/Eight of the \d+ recorded decisions/)).toBeInTheDocument();
    expect(screen.getByText(/--expect-lock config\/public_snapshot.lock.json/)).toBeInTheDocument();
  });

  it("adds live safeguard counts and the served fingerprint when governance loads", async () => {
    renderPage(() => Promise.resolve(new Response(JSON.stringify(governance), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));

    expect(await screen.findByText(/3 of 4 safeguards are enforced in code; the remaining one needs operator evidence\./)).toBeInTheDocument();
    const fingerprint = screen.getByText("aaaaaaaaaaaaaaaa…");
    expect(fingerprint).toHaveAttribute("title", governance.published_snapshot.sha256);
    // The shortened fingerprint sits mid-sentence, so its ellipsis never meets a full stop.
    expect(fingerprint.parentElement).toHaveTextContent("The live snapshot fingerprint, aaaaaaaaaaaaaaaa…, must match the lock in the repository.");
  });
});
