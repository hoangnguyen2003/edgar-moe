import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GovernancePage } from "./GovernancePage";

const governance = {
  schema_version: 1,
  frozen_v1: {
    path: "data/demo/snapshot.json",
    sha256: "06aa652c638d12400f5e8210778b0d0842dab3c70cfbd59bbb84a67879dab7a5",
    data_mode: "authenticated_locked_test",
    as_of: "2026-07-31",
    selection_hash: "b".repeat(64),
    locked_test_hash: "c".repeat(64),
    research_only: true,
  },
  public_data: { raw_sources_public: false, derived_output_public: true, redistribution_status: "operator_review_required" },
  controls: [
    { key: "frozen_v1_identity", status: "enforced", owner: "repository", summary: "Content-addressed." },
    { key: "pre_entry_forecasts", status: "enforced", owner: "repository", summary: "Saved before entry." },
    { key: "append_only_outcomes", status: "enforced", owner: "repository", summary: "Appended only." },
    { key: "provider_operations", status: "pending_operator_evidence", owner: "operator", summary: "Needs an exercise." },
  ],
  forward_status: { configured: false, available: false },
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
    expect(screen.getByText("Public")).toHaveClass("tag--good", "tag--quiet");
    expect(screen.getAllByText("Enforced")).toHaveLength(3);
    expect(screen.getAllByText("Enforced")[0]).toHaveClass("tag", "tag--good");
    expect(screen.getByText("Frozen v1")).toHaveClass("tag", "tag--stamp");
  });
});
