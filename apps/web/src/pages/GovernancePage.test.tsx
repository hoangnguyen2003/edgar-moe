import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GovernancePage } from "./GovernancePage";

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
    { key: "published_snapshot_identity", status: "enforced", owner: "repository", summary: "Synthetic fixture is content-addressed." },
    { key: "pre_entry_forecasts", status: "enforced", owner: "repository", summary: "Saved before entry." },
    { key: "append_only_outcomes", status: "enforced", owner: "repository", summary: "Appended only." },
    { key: "prospective_publication", status: "withheld_review", owner: "repository", summary: "Withheld pending rights review." },
    { key: "provider_operations", status: "pending_operator_evidence", owner: "operator", summary: "Needs an exercise." },
  ],
  forward_status: { public_visibility: "withheld_review", message: "Prospective forecasts and outcomes are withheld from the public application pending source-rights review. The registry remains private." },
};

function renderPage() {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify(governance), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }))));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><GovernancePage /></QueryClientProvider>);
}

describe("Audit page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("marks what still needs a person as a warning, and what is settled quietly", async () => {
    renderPage();

    // The one open review is a warning tag, like the one safeguard still needing evidence.
    const required = await screen.findByText("Required");
    expect(required).toHaveClass("tag", "tag--warning");
    expect(required).not.toHaveClass("tag--quiet");
    expect(screen.getByText("Needs operator evidence")).toHaveClass("tag", "tag--warning");
    // Settled facts are words in their tone, not boxes.
    expect(screen.getByText("Private")).toHaveClass("tag--good", "tag--quiet");
    expect(screen.getAllByText("Synthetic demo")[0]).toHaveClass("tag", "tag--stamp");
    expect(screen.getAllByText("Withheld")[0]).toHaveClass("tag", "tag--good", "tag--quiet");
    expect(screen.getAllByText("Enforced")).toHaveLength(3);
    expect(screen.getAllByText("Enforced")[0]).toHaveClass("tag", "tag--good");
    expect(screen.getByText("Withheld for review")).toHaveClass("tag", "tag--warning");
    expect(screen.getAllByText("Withheld")).toHaveLength(2);
    expect(screen.getByText(/older reports may remain in the public repository/)).toBeInTheDocument();
  });
});
