import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExperimentRecord, ResearchEvidenceResponse } from "../lib/types";
import { ResearchPage } from "./ResearchPage";

function candidate(index: number, rankIc: number, rmse: number, selected = false): ExperimentRecord {
  return {
    name: `Shrinkage-Gated MoE h=${index} dropout=0.10`,
    family: "moe",
    validation_rmse: rmse,
    validation_rank_ic: rankIc,
    selected,
  };
}

// Twelve candidates: the selected one ranks 11th by rank IC and 1st by RMSE.
const experiments: ExperimentRecord[] = [
  ...Array.from({ length: 10 }, (_, index) => candidate(index + 1, 0.2 - index * 0.01, 0.08 + index * 0.001)),
  { name: "Fundamental-Anchored MoE h=64 dropout=0.10 gate=1.00 moe=0.25", family: "anchored", validation_rmse: 0.07, validation_rank_ic: 0.05, selected: true },
  { name: "Elastic Net", family: "linear", validation_rmse: 0.09, validation_rank_ic: -0.08, selected: false },
];

function jsonResponse(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }));
}

const pendingEvidence: ResearchEvidenceResponse = {
  schema_version: 1,
  catalog_sha256: "a".repeat(64),
  frozen_v1: {
    status: "frozen_locked_test",
    dataset_id: "v1-frozen",
    as_of: "2026-07-31",
    selection_hash: "b".repeat(64),
    locked_test_hash: "c".repeat(64),
    snapshot_sha256: "d".repeat(64),
    total_events: 1794,
    validation_events: 500,
    locked_test_events: 600,
    locked_rank_ic: 0.031624,
    locked_rank_ic_interval_95: { low: -0.0114, high: 0.0702, method: "two_calendar_month_moving_block", calendar_months: 24, resamples: 1000, source_sha256: "e".repeat(64) },
    portfolio_10bps_sharpe: -0.632,
    candidate_universe: {
      status: "retrospective_test_period_screen",
      screen_as_of: "2026-07-31",
      first_validation_start: "2023-01-01",
      locked_test_start: "2025-01-01",
      interpretation: "The candidate list used locked-test-period liquidity; this is not a fully point-in-time historical evaluation.",
    },
    interpretation: "Positive skill or tradable alpha is not established.",
  },
  duration_aware_v2: { status: "pending_review", reason: "No reviewed v2 aggregate." },
};

function renderPage(evidence: ResearchEvidenceResponse | "error" = pendingEvidence) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const path = new URL(String(input), "https://terminal.example").pathname;
    if (path === "/api/v1/experiments") return jsonResponse(experiments);
    if (path === "/api/v1/research-evidence") return evidence === "error"
      ? Promise.resolve(new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }))
      : jsonResponse(evidence);
    return jsonResponse({ metadata: { data_mode: "authenticated_locked_test" } });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ResearchPage />
    </QueryClientProvider>,
  );
}

function bodyRows() {
  return within(document.querySelector(".leaderboard-panel table") as HTMLElement).getAllByRole("row").slice(1);
}

describe("Reviewed research evidence", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows frozen uncertainty and an explicit pending v2 state", async () => {
    renderPage();
    const panel = await screen.findByRole("region", { name: "Evidence and uncertainty" });
    expect(panel).toHaveTextContent("−0.011 to +0.070");
    expect(panel).toHaveTextContent("10 bps cost-aware Sharpe");
    expect(panel).toHaveTextContent("Universe-selection limitation");
    expect(panel).toHaveTextContent("locked-test-period liquidity");
    expect(panel).toHaveTextContent("2026-07-31");
    expect(panel).toHaveTextContent("Pending review. No v2 comparison");
    expect(panel).not.toHaveTextContent("v2 beat");
  });

  it("does not hide the leaderboard if the evidence endpoint fails", async () => {
    renderPage("error");
    expect(await screen.findByRole("alert")).toHaveTextContent("Reviewed evidence is unavailable");
    expect(document.querySelector(".leaderboard-panel table")).toBeInTheDocument();
  });

  it("labels reviewed v2 comparisons as development-only and surfaces cost assumptions", async () => {
    const reviewed: ResearchEvidenceResponse = {
      ...pendingEvidence,
      duration_aware_v2: {
        status: "reviewed_pretest", dataset_id: "v2", source_manifest_sha256: "f".repeat(64), selection_sha256: "1".repeat(64),
        review_sha256: "2".repeat(64), oof_events: 100, champion_name: "Example model", champion_weighted_rank_ic: 0.02,
        candidate_universe_status: "historical_membership_unverified",
        uncertainty_method: "paired_calendar_month_moving_block_within_fold", block_months: 2, bootstrap_resamples: 1000,
        comparisons: [{ baseline: "Elastic Net", rank_ic_delta: 0.01, interval_status: "ready", interval_low: -0.02, interval_high: 0.04 }],
        portfolio_status: "development_only", cost_scenarios: [{ model: "Example model", cost_bps: 10, sharpe: -0.2, annualized_return: -0.03 }],
        cost_definition: "10/25/50 bps per unit of one-sided turnover plus configured short borrow",
        approval_reference: "review/12345", interpretation: "Conditional on selection; not independent evidence.",
      },
    };
    renderPage(reviewed);
    const panel = await screen.findByRole("region", { name: "Evidence and uncertainty" });
    expect(panel).toHaveTextContent("Conditional on selection; not independent evidence.");
    expect(panel).toHaveTextContent("Historical candidate membership is unverified");
    expect(within(panel).getByRole("table", { name: /Development-fold rank IC difference/ })).toHaveTextContent("Elastic Net");
    expect(within(panel).getByRole("table", { name: /Development-fold cost scenarios/ })).toHaveTextContent("10 bps");
  });
});

describe("Experiment leaderboard", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("answers with the chosen model and its place in the ranking; its settings sit in its row", async () => {
    renderPage();
    await screen.findByRole("table");
    const answer = document.querySelector(".page-header__answer")!;
    expect(answer).toHaveTextContent("Of 12 candidates, Fundamental-Anchored MoE was chosen");
    expect(answer).toHaveTextContent("(#11 of 12)");
    expect(within(answer as HTMLElement).getByText("Fundamental-Anchored MoE").tagName).toBe("MARK");
    const chosenRow = bodyRows().find((row) => row.classList.contains("is-selected"))!;
    expect(within(chosenRow).getByText("gate=1.00")).toBeInTheDocument();
  });

  it("ranks by rank IC and pins the selection when it falls below the preview", async () => {
    renderPage();
    await screen.findByRole("table");
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: /rank IC/ })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("button", { name: "Ranking skill" })).toHaveAttribute("aria-pressed", "true");

    const rows = bodyRows();
    expect(rows[0]).toHaveTextContent("+0.200");
    const selected = rows.find((row) => row.classList.contains("is-selected"))!;
    expect(selected).toHaveTextContent("11");
    expect(selected).toHaveTextContent("Chosen");
    expect(within(table).queryByText("Elastic Net")).not.toBeInTheDocument();
  });

  it("explains the settings codes and shows where each simpler model finished", async () => {
    renderPage();
    await screen.findByRole("table");
    const key = screen.getByText("Settings").closest(".settings-key") as HTMLElement;
    for (const code of ["h", "dropout", "gate", "moe"]) {
      expect(within(key).getByText(code).tagName).toBe("DT");
    }
    expect(key).toHaveTextContent("0 keeps them fixed");

    // The comparison states its outcome instead of leaving it below the preview.
    const reasons = screen.getByRole("heading", { name: "Why compare against simpler models?" }).closest("article")!;
    const item = (title: string) => within(reasons).getByText(title).closest("li")!;
    expect(item("Linear model")).toHaveTextContent("Elastic Net · #12 of 12 · \u22120.080");
    expect(item("Mixture of experts")).toHaveTextContent("Fundamental-Anchored MoE · #11 of 12 · +0.050");
    // No tree model in this registry, so no outcome is invented for one.
    expect(item("Tree model").querySelector(".reason-list__outcome")).toBeNull();
  });

  it("re-sorts by prediction error and expands to every candidate", async () => {
    renderPage();
    await screen.findByRole("table");

    fireEvent.click(screen.getByRole("button", { name: "Prediction error" }));
    expect(screen.getByRole("button", { name: "Prediction error" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("columnheader", { name: /RMSE/ })).toHaveAttribute("aria-sort", "ascending");
    expect(bodyRows()[0]).toHaveClass("is-selected");
    expect(bodyRows()[0]).toHaveTextContent("lowest");

    fireEvent.click(screen.getByRole("button", { name: "Show all 12 candidates" }));
    expect(bodyRows()).toHaveLength(12);
    expect(screen.getByText("Elastic Net")).toBeInTheDocument();
  });
});
