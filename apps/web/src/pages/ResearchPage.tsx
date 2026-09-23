import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { Takeaway } from "../components/Takeaway";
import { api } from "../lib/api";
import { decimal, signedDecimal, splitModelName } from "../lib/format";
import type { ExperimentRecord } from "../lib/types";

type SortKey = "rank_ic" | "rmse";
const PREVIEW_ROWS = 10;

function compareNumbers(a: number, b: number): number {
  return a === b ? 0 : a < b ? -1 : 1;
}

const sorters: Record<SortKey, (a: ExperimentRecord, b: ExperimentRecord) => number> = {
  rank_ic: (a, b) => compareNumbers(b.validation_rank_ic ?? -Infinity, a.validation_rank_ic ?? -Infinity),
  rmse: (a, b) => compareNumbers(a.validation_rmse, b.validation_rmse),
};

function ranked(rows: ExperimentRecord[], sort: SortKey) {
  return [...rows]
    .sort((a, b) => sorters[sort](a, b) || a.name.localeCompare(b.name))
    .map((row, index) => ({ row, rank: index + 1 }));
}

export function ResearchPage() {
  const experiments = useQuery({ queryKey: ["experiments"], queryFn: api.experiments });
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  if (experiments.isLoading || summary.isLoading) return <div className="page"><LoadingState label="Loading the model comparison" skeleton={["figures", "rows"]} /></div>;
  if (experiments.error) return <div className="page"><ErrorState error={experiments.error} onRetry={() => void experiments.refetch()} /></div>;
  const rows = experiments.data!;
  const selected = rows.find((row) => row.selected);
  const selectedName = selected ? splitModelName(selected.name) : null;
  const selectedRank = ranked(rows, "rank_ic").find((entry) => entry.row.selected)?.rank;
  const finalIc = summary.data?.predictive_metrics?.locked_test?.rank_ic;
  return (
    <div className="page">
      <PageHeader title="Model comparison">
        {rows.length} candidate models learned from the same filings and were compared on 2023–2024 validation data.
        The chosen one was frozen before the final test.
      </PageHeader>
      {selected && selectedName && (
        <Takeaway variant="quiet" label="Chosen model" title={selectedName.family}>
          <p>
            Picked for the best ranking skill in its weaker validation year: the most consistent candidate, not simply
            the highest average. It ranks #{selectedRank} of {rows.length} on average ranking skill below.
          </p>
          {selectedName.params.length > 0 && <ParamList params={selectedName.params} />}
        </Takeaway>
      )}
      <section className="figures figures--three" aria-label="Chosen model results">
        <MetricCard label="Ranking skill, validation" info="rankIc" value={decimal(selected?.validation_rank_ic, 3)} detail="2023–2024; higher is better" />
        <MetricCard label="Prediction error, validation" info="rmse" value={decimal(selected?.validation_rmse, 5)} detail="2023–2024; lower is better" />
        <MetricCard label="Ranking skill, final test" info="lockedTest" value={decimal(finalIc, 3)} detail="2025–2026, scored once after freezing" />
      </section>
      <Leaderboard rows={rows} />
      <article className="panel">
        <header>
          <div>
            <h2>Why compare against simpler models?</h2>
            <p>The mixture of experts only earns its complexity if it beats plainer approaches on the same data.</p>
          </div>
        </header>
        <ol className="reason-list">
          <li><strong>Linear model</strong><p>Checks whether a simple weighted sum of the same inputs does just as well.</p></li>
          <li><strong>Tree model</strong><p>Checks what a flexible model finds without the specialists and the gate.</p></li>
          <li><strong>Mixture of experts</strong><p>Has to beat both on ranking skill, and then hold up in the cost-aware backtest.</p></li>
        </ol>
      </article>
      {summary.data?.metadata.data_mode === "synthetic_fixture" && <DemoNotice />}
    </div>
  );
}

function Leaderboard({ rows }: { rows: ExperimentRecord[] }) {
  const [sort, setSort] = useState<SortKey>("rank_ic");
  const [expanded, setExpanded] = useState(false);
  const sortLabelId = useId();
  const entries = ranked(rows, sort);
  const visible = expanded ? entries : entries.slice(0, PREVIEW_ROWS);
  // Keep the frozen selection on screen even when it ranks below the preview.
  const pinned = expanded ? undefined : entries.find((entry) => entry.row.selected && entry.rank > PREVIEW_ROWS);
  const maxIc = Math.max(...rows.map((row) => Math.abs(row.validation_rank_ic ?? 0)), Number.EPSILON);
  const bestRmse = Math.min(...rows.map((row) => row.validation_rmse));
  const sortLabel = sort === "rank_ic" ? "ranking skill, highest first" : "prediction error, lowest first";
  return (
    <article className="panel leaderboard-panel">
      <header>
        <div>
          <h2>All {rows.length} candidates</h2>
          <p>Validation results for every model tried, sorted by {sortLabel}. The chosen model is highlighted.</p>
        </div>
        <div className="control">
          <span className="control__label" id={sortLabelId}>Sort by</span>
          <div className="segmented" role="group" aria-labelledby={sortLabelId}>
            <button type="button" aria-pressed={sort === "rank_ic"} onClick={() => setSort("rank_ic")}>Ranking skill</button>
            <button type="button" aria-pressed={sort === "rmse"} onClick={() => setSort("rmse")}>Prediction error</button>
          </div>
        </div>
      </header>
      <div className="table-scroll">
        <table className="leaderboard" role="table">
          <caption className="sr-only">Validation results for {rows.length} candidate models, sorted by {sortLabel}.</caption>
          <thead role="rowgroup">
            <tr role="row">
              <th scope="col" role="columnheader" className="leaderboard__rank">#</th>
              <th scope="col" role="columnheader">Model and settings</th>
              <th scope="col" role="columnheader" aria-sort={sort === "rank_ic" ? "descending" : "none"}>
                Ranking skill (rank IC) <small>higher is better</small>
              </th>
              <th scope="col" role="columnheader" aria-sort={sort === "rmse" ? "ascending" : "none"}>
                Error (RMSE) <small>lower is better</small>
              </th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            {visible.map((entry) => <LeaderboardRow key={entry.row.name} {...entry} maxIc={maxIc} bestRmse={bestRmse} />)}
            {pinned && (
              <>
                <tr className="leaderboard__gap" aria-hidden="true"><td colSpan={4}>⋯</td></tr>
                <LeaderboardRow {...pinned} maxIc={maxIc} bestRmse={bestRmse} />
              </>
            )}
          </tbody>
        </table>
      </div>
      {rows.length > PREVIEW_ROWS && (
        <button type="button" className="text-button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
          {expanded ? `Show top ${PREVIEW_ROWS} only` : `Show all ${rows.length} candidates`}
        </button>
      )}
    </article>
  );
}

function LeaderboardRow({ row, rank, maxIc, bestRmse }: {
  row: ExperimentRecord;
  rank: number;
  maxIc: number;
  bestRmse: number;
}) {
  const { family, params } = splitModelName(row.name);
  const ic = row.validation_rank_ic;
  const width = ic == null ? 0 : (Math.abs(ic) / maxIc) * 50;
  const delta = row.validation_rmse - bestRmse;
  return (
    <tr role="row" className={row.selected ? "is-selected" : undefined}>
      <td role="cell" className="leaderboard__rank">{rank}</td>
      <td role="cell">
        <div className="leaderboard__model">
          <strong>{family}</strong>
          {row.selected && <span className="badge badge--chosen">Chosen</span>}
        </div>
        {params.length > 0 && <ParamList params={params} />}
      </td>
      <td role="cell" data-label="Ranking skill">
        <div className="ic-cell">
          <span className="ic-bar" aria-hidden="true">
            <i style={ic != null && ic < 0 ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }} />
          </span>
          <span className="num">{signedDecimal(ic, 3)}</span>
        </div>
      </td>
      <td role="cell" data-label="Error (RMSE)">
        <span className="num">{decimal(row.validation_rmse, 5)}</span>
        <small className="leaderboard__delta">{delta === 0 ? "lowest" : `+${decimal(delta, 5)} vs lowest`}</small>
      </td>
    </tr>
  );
}

function ParamList({ params }: { params: string[] }) {
  return <ul className="param-list" aria-label="Settings">{params.map((param) => <li key={param}>{param}</li>)}</ul>;
}

function DemoNotice() {
  return (
    <div className="notice">
      <div>
        <strong>Synthetic test data</strong>
        <span>These values come from a generated dataset that checks the software works. They say nothing about real markets.</span>
      </div>
    </div>
  );
}
