import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExperimentRecord } from "../lib/types";
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

function renderPage() {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const path = new URL(String(input), "https://terminal.example").pathname;
    if (path === "/api/v1/experiments") return jsonResponse(experiments);
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
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

describe("Experiment leaderboard", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("leads with the chosen model, its settings as chips, and its place in the ranking", async () => {
    renderPage();
    const chosen = await screen.findByRole("region", { name: "Chosen model" });
    expect(within(chosen).getByText("Fundamental-Anchored MoE")).toBeInTheDocument();
    expect(within(chosen).getByText("gate=1.00")).toBeInTheDocument();
    expect(chosen).toHaveTextContent("It ranks #11 of 12");
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
