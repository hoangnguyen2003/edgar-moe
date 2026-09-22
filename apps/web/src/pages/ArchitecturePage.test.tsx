import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../lib/router";
import { ArchitecturePage } from "./ArchitecturePage";

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
    { key: "frozen_v1_identity", status: "enforced", owner: "repository", summary: "" },
    { key: "pre_entry_forecasts", status: "enforced", owner: "repository", summary: "" },
    { key: "append_only_outcomes", status: "enforced", owner: "repository", summary: "" },
    { key: "provider_operations", status: "pending_operator_evidence", owner: "operator", summary: "" },
  ],
  forward_status: { configured: false, available: false },
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
    expect(titles).toEqual(["Research study", "Public site", "Live tracking", "Evidence copilot", "Delivery and checks"]);
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
    expect(screen.getByText(/--expect-lock config\/public_snapshot.lock.json/)).toBeInTheDocument();
  });

  it("adds live safeguard counts and the served fingerprint when governance loads", async () => {
    renderPage(() => Promise.resolve(new Response(JSON.stringify(governance), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));

    expect(await screen.findByText(/3 of 4 safeguards are enforced in code/)).toBeInTheDocument();
    expect(screen.getByText("06aa652c638d1240…")).toHaveAttribute("title", governance.frozen_v1.sha256);
  });
});
